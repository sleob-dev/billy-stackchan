# Construit le firmware maison depuis Windows : envoie les fichiers sur une machine Linux
# (Debian ou Ubuntu, joignable en SSH en root), y lance construire.sh, puis rapatrie les
# binaires. Ne flashe rien.
#
#   powershell -ExecutionPolicy Bypass -File .\construire.ps1 -Hote root@192.0.2.20
#   powershell -ExecutionPolicy Bypass -File .\construire.ps1 -Hote root@192.0.2.20 -OtaUrl http://192.0.2.10:8003/xiaozhi/ota/
#
# -OtaUrl est facultatif : sans lui, on branche le robot sur son serveur par la clé NVS
# wifi:ota_url (outils\nvs_ota_url.py), sans adresse compilée.
param(
    [Parameter(Mandatory = $true)][string]$Hote,
    [string]$Destination = (Join-Path $PSScriptRoot 'sortie'),
    [string]$OtaUrl = ''
)
$ErrorActionPreference = 'Continue'
$ici = $PSScriptRoot

function Sur-Hote([string]$commande) {
    & ssh $Hote $commande
    if ($LASTEXITCODE -ne 0) { throw "Échec sur $Hote : $commande" }
}

# Envoi : le script, la surcouche sdkconfig, les correctifs, la skin et les ajouts
Sur-Hote 'rm -rf /opt/billy-fw/patches /opt/billy-fw/patches-stackchan /opt/billy-fw/skin-billy /opt/billy-fw/billy-extras; mkdir -p /opt/billy-fw'
$envoi = @((Join-Path $ici 'construire.sh'), (Join-Path $ici 'sdkconfig.defaults.local'))
foreach ($dossier in 'patches', 'patches-stackchan', 'skin-billy', 'billy-extras') {
    $chemin = Join-Path $ici $dossier
    if (Test-Path -LiteralPath $chemin) { $envoi += $chemin }
}
& scp -q -r @envoi "${Hote}:/opt/billy-fw/"
if ($LASTEXITCODE -ne 0) { throw "Envoi des fichiers vers $Hote impossible" }

Write-Host 'Compilation (une dizaine de minutes la première fois, ESP-IDF compris)…'
if ($OtaUrl) {
    if ($OtaUrl -notmatch '^https?://[A-Za-z0-9.:/_-]+$') { throw "Adresse OTA invalide : $OtaUrl" }
    Sur-Hote "BILLY_OTA_URL=$OtaUrl bash /opt/billy-fw/construire.sh"
} else {
    Sur-Hote 'bash /opt/billy-fw/construire.sh'
}

Sur-Hote 'tar -C /opt/billy-fw/sortie -cf /tmp/billy-fw-sortie.tar .'
$archive = Join-Path $env:TEMP 'billy-fw-sortie.tar'
& scp -q "${Hote}:/tmp/billy-fw-sortie.tar" $archive
if ($LASTEXITCODE -ne 0) { throw 'Rapatriement des binaires impossible' }
Sur-Hote 'rm -f /tmp/billy-fw-sortie.tar'

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
# Le build précédent est gardé dans precedent\ (un seul niveau) avant d'être remplacé.
if (Test-Path -LiteralPath (Join-Path $Destination 'stack-chan.bin')) {
    $precedent = Join-Path $Destination 'precedent'
    if (Test-Path -LiteralPath $precedent) { Remove-Item -Recurse -LiteralPath $precedent }
    New-Item -ItemType Directory -Path $precedent | Out-Null
    Get-ChildItem -LiteralPath $Destination -File | Move-Item -Destination $precedent
}
& "$env:SystemRoot\System32\tar.exe" -xf $archive -C $Destination
if ($LASTEXITCODE -ne 0) { throw 'Extraction impossible' }
Remove-Item -LiteralPath $archive

# Contrôle des empreintes une fois sur le PC.
$erreurs = 0
foreach ($ligne in Get-Content (Join-Path $Destination 'sha256sums.txt')) {
    $attendu, $nom = $ligne -split '\s+\*?', 2
    $nom = $nom -replace '^\./', ''
    $calcule = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Destination $nom)).Hash.ToLower()
    if ($calcule -ne $attendu) { Write-Host "Empreinte fausse : $nom"; $erreurs++ }
}
if ($erreurs -gt 0) { throw 'Binaires abîmés pendant le transfert' }
Write-Host "Binaires prêts dans $Destination :"
Get-Content (Join-Path $Destination 'adresses.txt')
