/*
 * Visage de Billy : moteur de dessin du firmware maison.
 *
 * Reprend les formes de la maquette « Visages de Billy » (styles A Visière,
 * D Kawaii, E Visière kawaii) sur un écran de 320 × 240. Les formes sont rastérisées avec
 * anticrénelage dans une image RGB565 en PSRAM, que l'avatar affiche par un canvas LVGL.
 */
#pragma once
#include <cstddef>
#include <cstdint>
#include <utility>
#include <vector>

namespace billy {

enum class Look { A, D, E };

enum class Expr {
    Neutre, Content, Rire, Triste, Pleure, Colere, Doute, Surpris,
    Reflechit, Amoureux, Clin, Timide, Etourdi, Endormi, Cool,
};

struct FaceState {
    Look look   = Look::E;
    Expr expr   = Expr::Neutre;
    int blink   = 0;     // 0 ouvert, 1 mi-clos, 2 fermé
    float talk  = -1.f;  // -1 : pas de parole, sinon ouverture de 0 à 4
    float drop  = 1.f;   // lunettes « Deal with it » : 0 hors écran, 1 posées
    float gazeX = 0.f;   // décalage des yeux (px)
    float gazeY = 0.f;
    float mouthY = 0.f;  // décalage de la bouche (px)
    bool eyesOn  = true;
    bool mouthOn = true;

    bool operator==(const FaceState& o) const
    {
        return look == o.look && expr == o.expr && blink == o.blink && talk == o.talk && drop == o.drop &&
               gazeX == o.gazeX && gazeY == o.gazeY && mouthY == o.mouthY && eyesOn == o.eyesOn &&
               mouthOn == o.mouthOn;
    }
    bool operator!=(const FaceState& o) const
    {
        return !(*this == o);
    }
};

struct Pt {
    float x, y;
};

// Allocateur en PSRAM : sans lui, les petits tableaux (< 512 octets) iraient en RAM interne,
// déjà très juste sur Billy, et seraient alloués à chaque image.
void* psram_alloc(size_t n);
void psram_free(void* p);
template <class T>
struct PsramAllocator {
    using value_type = T;
    PsramAllocator() = default;
    template <class U>
    PsramAllocator(const PsramAllocator<U>&)
    {
    }
    T* allocate(size_t n)
    {
        return static_cast<T*>(psram_alloc(n * sizeof(T)));
    }
    void deallocate(T* p, size_t)
    {
        psram_free(p);
    }
    template <class U>
    bool operator==(const PsramAllocator<U>&) const
    {
        return true;
    }
    template <class U>
    bool operator!=(const PsramAllocator<U>&) const
    {
        return false;
    }
};
using Line = std::vector<Pt, PsramAllocator<Pt>>;      // ligne brisée ou polygone
using Path = std::vector<Line, PsramAllocator<Line>>;  // une ou plusieurs lignes

class FaceRenderer {
public:
    static constexpr int W = 320;
    static constexpr int H = 240;

    struct Rect {
        int x0, y0, x1, y1;  // bornes incluses ; vide si x1 < x0
        bool empty() const
        {
            return x1 < x0 || y1 < y0;
        }
    };

    ~FaceRenderer();
    bool begin();  // alloue les deux images et le masque en PSRAM

    // Image affichée (tampon du canvas LVGL)
    uint16_t* pixels() const
    {
        return _shown;
    }
    struct Dirty {
        Rect r[3];
        int n = 0;
        void add(const Rect& z)
        {
            if (n < 3) {
                r[n++] = z;
            }
        }
    };
    // Dessine dans l'image de travail et renvoie les zones qui ont pu changer : tout l'écran
    // quand l'expression ou le style changent, sinon les zones des yeux (clignement) et de la
    // bouche (parole), recopiées depuis le fond puis redessinées.
    Dirty render(const FaceState& s);
    // Recopie dans l'image affichée ce qui a changé dans « zone », et renvoie le rectangle exact :
    // l'écran ne rafraîchit que lui.
    Rect present(const Rect& zone);

private:
    struct Style {
        uint32_t bg, ink, acc, pink, warn, water, tongue, iris;
        float sw, cheek;
        bool kawaii, hasIris;
    };
    static Style style(Look look);

    void fill(const Path& p, uint32_t rgb, float opacity = 1.f);
    void stroke(const Path& p, uint32_t rgb, float width, bool closed = false);
    void rasterize(const Line& poly);
    void blend(uint32_t rgb, float opacity);
    void clearMask();
    void drawBase(const FaceState& st, bool withShades);
    static Rect mouthZone(bool kawaii);
    static Rect eyeZone(bool kawaii, int side);
    void restore(const Rect& z);

    void eye(const Style& S, int side, int kind, const FaceState& st);
    void mouth(const Style& S, int kind, const FaceState& st);
    void shades(const Style& S, float drop);

    uint16_t* _px    = nullptr;  // image de travail
    uint16_t* _shown = nullptr;  // image affichée
    uint16_t* _base  = nullptr;  // fond, joues et accessoires, sans les yeux ni la bouche
    FaceState _bs;               // état de l'image de base
    bool _baseValid = false;
    std::vector<std::pair<float, int>, PsramAllocator<std::pair<float, int>>> _xs;  // croisements (réutilisé)
    Line _cap;  // capsule d'un segment de trait (réutilisée)
    uint8_t* _mask   = nullptr;
    float* _row     = nullptr;
    int _bx0 = W, _by0 = H, _bx1 = -1, _by1 = -1;  // zone touchée du masque
};

}  // namespace billy
