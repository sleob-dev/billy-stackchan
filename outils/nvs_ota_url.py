"""Ajoute, remplace ou efface la clé wifi:ota_url dans une image de la partition NVS du robot.

Le firmware XiaoZhi lit cette clé avant l'adresse OTA compilée (CONFIG_OTA_URL) : c'est elle
qui envoie le robot vers votre serveur plutôt que vers api.tenclass.net. L'outil écrit
directement au format NVS d'ESP-IDF (pages de 4 Ko, entrées de 32 octets), sans réécrire les
autres réglages (Wi-Fi, calibration des servos, compte).

  python nvs_ota_url.py nvs.bin                       affiche la clé actuelle
  python nvs_ota_url.py nvs.bin --ecrire URL -o new   écrit (ou remplace) la clé
  python nvs_ota_url.py nvs.bin --effacer -o new      retour au serveur d'usine

La lecture et l'écriture de la partition (0x9000, 0x4000 octets) se font avec esptool :
voir le README, section « Brancher le robot sur votre serveur ».
"""

import argparse
import struct
import sys
import zlib

PAGE_SIZE = 4096
ENTRY_SIZE = 32
ENTRIES_PER_PAGE = 126
FIRST_ENTRY = 64  # en-tête (32) + table d'état des entrées (32)
PAGE_ACTIVE, PAGE_FULL = 0xFFFFFFFE, 0xFFFFFFFC
ENTRY_EMPTY, ENTRY_WRITTEN, ENTRY_ERASED = 3, 2, 0
TYPE_U8, TYPE_STR = 0x01, 0x21
NAMESPACE, KEY = "wifi", "ota_url"


def crc32(data: bytes, crc: int = 0xFFFFFFFF) -> int:
    return zlib.crc32(data, crc) & 0xFFFFFFFF


def item_crc(item: bytes) -> int:
    """CRC d'une entrée : tout sauf le champ CRC lui-même (octets 4 à 8)."""
    return crc32(item[8:32], crc32(item[0:4]))


class Page:
    def __init__(self, image: bytearray, number: int):
        self.image, self.base = image, number * PAGE_SIZE
        self.state, self.seq = struct.unpack_from("<II", image, self.base)

    def entry_state(self, i: int) -> int:
        word = struct.unpack_from("<I", self.image, self.base + 32 + 4 * (i // 16))[0]
        return (word >> (2 * (i % 16))) & 3

    def set_entry_state(self, i: int, state: int) -> None:
        pos = self.base + 32 + 4 * (i // 16)
        word = struct.unpack_from("<I", self.image, pos)[0]
        shift = 2 * (i % 16)
        struct.pack_into("<I", self.image, pos, (word & ~(3 << shift)) | (state << shift))

    def entry(self, i: int) -> bytes:
        pos = self.base + FIRST_ENTRY + ENTRY_SIZE * i
        return bytes(self.image[pos:pos + ENTRY_SIZE])

    def items(self):
        """(index, nsIndex, type, span, clé, entrée) des entrées écrites."""
        i = 0
        while i < ENTRIES_PER_PAGE:
            if self.entry_state(i) != ENTRY_WRITTEN:
                i += 1
                continue
            e = self.entry(i)
            ns, typ, span = e[0], e[1], e[2]
            yield i, ns, typ, span, e[8:24].split(b"\0")[0].decode(errors="replace"), e
            i += max(span, 1)


def pages(image: bytearray):
    return [Page(image, n) for n in range(len(image) // PAGE_SIZE)]


def check_crcs(image: bytearray) -> int:
    """Vérifie le CRC de chaque entrée écrite : prouve que le calcul est le bon."""
    bad = 0
    for page in pages(image):
        if page.state not in (PAGE_ACTIVE, PAGE_FULL):
            continue
        for i, ns, typ, span, key, e in page.items():
            if struct.unpack_from("<I", e, 4)[0] != item_crc(e):
                print(f"CRC faux : page {page.base // PAGE_SIZE}, entrée {i} ({key})", file=sys.stderr)
                bad += 1
            if typ == TYPE_STR:
                size, _, data_crc = struct.unpack_from("<HHI", e, 24)
                start = page.base + FIRST_ENTRY + ENTRY_SIZE * (i + 1)
                if crc32(bytes(image[start:start + size])) != data_crc:
                    print(f"CRC de texte faux : {key}", file=sys.stderr)
                    bad += 1
    return bad


def namespace_index(image: bytearray, name: str) -> int:
    for page in pages(image):
        if page.state in (PAGE_ACTIVE, PAGE_FULL):
            for _, ns, typ, _, key, e in page.items():
                if ns == 0 and typ == TYPE_U8 and key == name:
                    return e[24]
    raise SystemExit(f"espace de noms {name!r} introuvable")


def find_key(image: bytearray, ns_index: int):
    """(page, index, span, valeur) de chaque exemplaire de la clé."""
    found = []
    for page in pages(image):
        if page.state not in (PAGE_ACTIVE, PAGE_FULL):
            continue
        for i, ns, typ, span, key, e in page.items():
            if ns == ns_index and key == KEY and typ == TYPE_STR:
                size = struct.unpack_from("<H", e, 24)[0]
                start = page.base + FIRST_ENTRY + ENTRY_SIZE * (i + 1)
                found.append((page, i, span, bytes(image[start:start + size - 1]).decode()))
    return found


def erase(image: bytearray, ns_index: int) -> None:
    for page, i, span, _ in find_key(image, ns_index):
        for k in range(i, i + span):
            page.set_entry_state(k, ENTRY_ERASED)


def write(image: bytearray, ns_index: int, value: str) -> None:
    data = value.encode() + b"\0"
    span = 1 + (len(data) + ENTRY_SIZE - 1) // ENTRY_SIZE
    active = [p for p in pages(image) if p.state == PAGE_ACTIVE]
    if len(active) != 1:
        raise SystemExit("il faut exactement une page active dans la NVS")
    page = active[0]
    # On écrit après la dernière entrée non vide : les entrées effacées gardent leur place
    # jusqu'au prochain ménage de la NVS.
    first_free = 0
    for k in range(ENTRIES_PER_PAGE):
        if page.entry_state(k) != ENTRY_EMPTY:
            first_free = k + 1
    for i, _, _, span_, _, _ in page.items():
        first_free = max(first_free, i + max(span_, 1))
    if first_free + span > ENTRIES_PER_PAGE:
        raise SystemExit("plus de place dans la page active")
    header = bytearray(b"\xff" * ENTRY_SIZE)
    header[0:4] = bytes([ns_index, TYPE_STR, span, 0xFF])
    header[8:24] = KEY.encode().ljust(16, b"\0")
    header[24:32] = struct.pack("<HHI", len(data), 0xFFFF, crc32(data))
    header[4:8] = struct.pack("<I", item_crc(bytes(header)))
    body = data.ljust((span - 1) * ENTRY_SIZE, b"\xff")
    pos = page.base + FIRST_ENTRY + ENTRY_SIZE * first_free
    image[pos:pos + ENTRY_SIZE] = header
    image[pos + ENTRY_SIZE:pos + ENTRY_SIZE * span] = body
    for k in range(first_free, first_free + span):
        page.set_entry_state(k, ENTRY_WRITTEN)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("image", help="image de la partition NVS (0x4000 octets)")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--ecrire", metavar="URL")
    action.add_argument("--effacer", action="store_true")
    parser.add_argument("-o", "--sortie", help="image modifiée à écrire")
    args = parser.parse_args()

    image = bytearray(open(args.image, "rb").read())
    if len(image) % PAGE_SIZE:
        raise SystemExit("taille d'image inattendue")
    if check_crcs(image):
        raise SystemExit("l'image de départ a des CRC faux : rien n'est modifié")
    ns = namespace_index(image, NAMESPACE)
    current = find_key(image, ns)
    print(f"{NAMESPACE}:{KEY} actuel : {current[-1][3] if current else '(absent, adresse d usine)'}")
    if not (args.ecrire or args.effacer):
        return
    if not args.sortie:
        raise SystemExit("préciser -o pour l'image modifiée")
    erase(image, ns)
    if args.ecrire:
        write(image, ns, args.ecrire)
    if check_crcs(image):
        raise SystemExit("l'image produite a des CRC faux : rien n'est écrit")
    after = find_key(image, ns)
    print(f"{NAMESPACE}:{KEY} nouveau : {after[-1][3] if after else '(absent, adresse d usine)'}")
    open(args.sortie, "wb").write(image)


if __name__ == "__main__":
    main()
