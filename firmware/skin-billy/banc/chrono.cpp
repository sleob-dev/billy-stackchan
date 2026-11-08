// Chronomètre : image de base (changement d'expression) contre bouche seule (parole)
#include "billy_face.h"
#include <chrono>
#include <cstdio>
using namespace billy;
int main() {
    FaceRenderer f; f.begin();
    for (int l = 0; l < 3; ++l) {
        FaceState s; s.look = static_cast<Look>(l);
        auto t0 = std::chrono::steady_clock::now();
        for (int i = 0; i < 50; ++i) { s.expr = static_cast<Expr>(i % 14); { auto d = f.render(s); for (int z = 0; z < d.n; ++z) f.present(d.r[z]); } }
        auto t1 = std::chrono::steady_clock::now();
        s.expr = Expr::Neutre; { auto d = f.render(s); for (int z = 0; z < d.n; ++z) f.present(d.r[z]); }
        auto t2 = std::chrono::steady_clock::now();
        for (int i = 0; i < 200; ++i) { s.talk = (i % 9) / 2.f; { auto d = f.render(s); for (int z = 0; z < d.n; ++z) f.present(d.r[z]); } }
        auto t3 = std::chrono::steady_clock::now();
        printf("style %c : image complète %.0f µs, bouche seule %.0f µs\n", "ADE"[l],
               std::chrono::duration<double, std::micro>(t1 - t0).count() / 50,
               std::chrono::duration<double, std::micro>(t3 - t2).count() / 200);
    }
}
