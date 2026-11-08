// Banc d'essai du visage de Billy sur PC : une planche PPM par style (15 expressions + 5 animations)
#include "billy_face.h"
#include <cstdio>
#include <vector>
using namespace billy;
int main() {
    const int W = FaceRenderer::W, H = FaceRenderer::H, G = 8, C = 5, R = 4;
    FaceRenderer f;
    if (!f.begin()) return 1;
    const char* noms = "ADE";
    for (int l = 0; l < 3; ++l) {
        const int SW = C * (W + G) + G, SH = R * (H + G) + G;
        std::vector<unsigned char> img(SW * SH * 3, 40);
        for (int k = 0; k < 20; ++k) {
            FaceState s;
            s.look = static_cast<Look>(l);
            if (k < 15) s.expr = static_cast<Expr>(k);
            else if (k == 15) s.blink = 1;
            else if (k == 16) s.blink = 2;
            else s.talk = (k - 17) * 2.f;  // 0, 2, 4
            if (k == 14) s.drop = 1.f;
            { auto d = f.render(s); for (int z = 0; z < d.n; ++z) f.present(d.r[z]); }
            const int ox = G + (k % C) * (W + G), oy = G + (k / C) * (H + G);
            for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) {
                unsigned short p = f.pixels()[y * W + x];
                unsigned char* d = &img[((oy + y) * SW + ox + x) * 3];
                d[0] = ((p >> 11) & 31) * 255 / 31; d[1] = ((p >> 5) & 63) * 255 / 63; d[2] = (p & 31) * 255 / 31;
            }
        }
        char nom[32]; snprintf(nom, sizeof nom, "planche-%c.ppm", noms[l]);
        FILE* o = fopen(nom, "wb"); fprintf(o, "P6 %d %d 255\n", SW, SH); fwrite(img.data(), 1, img.size(), o); fclose(o);
    }
    return 0;
}
