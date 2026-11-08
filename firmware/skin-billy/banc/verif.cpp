// Vérifie les zones du dessin partiel : après une suite de clignements et de paroles dessinés
// zone par zone, l'image affichée doit être identique à un dessin complet du même état.
#include "billy_face.h"
#include <cstdio>
#include <cstring>
using namespace billy;

static void afficher(FaceRenderer& f, const FaceState& s)
{
    auto d = f.render(s);
    for (int z = 0; z < d.n; ++z) {
        f.present(d.r[z]);
    }
}

int main()
{
    const int N = FaceRenderer::W * FaceRenderer::H;
    int erreurs = 0, essais = 0;
    for (int l = 0; l < 3; ++l) {
        for (int e = 0; e < 15; ++e) {  // 14 : Cool, lunettes posées
            FaceRenderer partiel;
            partiel.begin();
            FaceState s;
            s.look = static_cast<Look>(l);
            s.expr = static_cast<Expr>(e);
            afficher(partiel, s);
            const int blinks[] = {1, 2, 1, 0, 2, 0};
            const float talks[] = {0, 4, 2, -1, 3.5f, 1, 4, -1};
            for (int k = 0; k < 8; ++k) {
                s.blink = blinks[k % 6];
                s.talk  = talks[k];
                afficher(partiel, s);
                FaceRenderer complet;
                complet.begin();
                afficher(complet, s);
                ++essais;
                if (memcmp(partiel.pixels(), complet.pixels(), N * 2) != 0) {
                    int diff = 0;
                    for (int i = 0; i < N; ++i) {
                        diff += partiel.pixels()[i] != complet.pixels()[i];
                    }
                    printf("ÉCART style %c, expression %d, clignement %d, parole %.1f : %d pixels\n", "ADE"[l], e,
                           s.blink, s.talk, diff);
                    ++erreurs;
                }
            }
        }
    }
    printf("%d essais, %d écarts\n", essais, erreurs);
    return erreurs ? 1 : 0;
}
