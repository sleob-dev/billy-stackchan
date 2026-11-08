/*
 * Visage de Billy : rastérisation anticrénelée (4 sous-lignes par pixel, couverture
 * horizontale exacte) et formes reprises telles quelles de la maquette du visage.
 */
#include "billy_face.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <esp_heap_caps.h>
#include <esp_log.h>

namespace billy {

namespace {

const char* TAG = "BillyFace";
constexpr float PI = 3.14159265f;

enum EyeKind { NONE, OPEN, WIDE, UP, HALF, ANGRY, SAD, HAPPY, SQUINT, XX, HEART, SLEEP, SHUT };
enum MouthKind { M_NONE, SMALL, SMILE, BIG, FLAT, FROWN, O, WAVY, SMIRK, CAT, TALK };
enum Extra : uint32_t { X_TEARS = 1, X_QUESTION = 2, X_BLUSH = 4, X_ZZZ = 8, X_SHADES = 16 };

struct ExprDef {
    int l, r, m;
    uint32_t x;
};

// Même table que les expressions de la maquette
const ExprDef kExpr[] = {
    {OPEN, OPEN, SMALL, 0},              // Neutre
    {HAPPY, HAPPY, SMILE, 0},            // Content
    {SQUINT, SQUINT, BIG, 0},            // Rire
    {SAD, SAD, FROWN, 0},                // Triste
    {SAD, SAD, WAVY, X_TEARS},           // Pleure
    {ANGRY, ANGRY, FROWN, 0},            // Colere
    {OPEN, HALF, SMIRK, 0},              // Doute
    {WIDE, WIDE, O, 0},                  // Surpris
    {UP, UP, FLAT, X_QUESTION},          // Reflechit
    {HEART, HEART, SMILE, X_BLUSH},      // Amoureux
    {OPEN, HAPPY, SMILE, 0},             // Clin
    {HAPPY, HAPPY, CAT, X_BLUSH},        // Timide
    {XX, XX, WAVY, 0},                   // Etourdi
    {SLEEP, SLEEP, SMALL, X_ZZZ},        // Endormi
    {OPEN, OPEN, SMIRK, X_SHADES},       // Cool
};

/* ------------------------------ Construction de chemins ------------------------------ */

Line ell(float cx, float cy, float rx, float ry)
{
    Line o;
    const int n = std::max(24, static_cast<int>((rx + ry) * 1.6f));
    o.reserve(n);
    const float c = cosf(2 * PI / n), s = sinf(2 * PI / n);
    float x = 1.f, y = 0.f;
    for (int i = 0; i < n; ++i) {
        o.push_back({cx + rx * x, cy + ry * y});
        const float nx = x * c - y * s;
        y = x * s + y * c;
        x = nx;
    }
    return o;
}

Line rect(float x, float y, float w, float h)
{
    return {{x, y}, {x + w, y}, {x + w, y + h}, {x, y + h}};
}

void quadTo(Line& o, Pt c, Pt p, int n = 14)
{
    Pt a = o.back();
    for (int i = 1; i <= n; ++i) {
        float t = static_cast<float>(i) / n, u = 1 - t;
        o.push_back({u * u * a.x + 2 * u * t * c.x + t * t * p.x, u * u * a.y + 2 * u * t * c.y + t * t * p.y});
    }
}

void cubicTo(Line& o, Pt c1, Pt c2, Pt p, int n = 18)
{
    Pt a = o.back();
    for (int i = 1; i <= n; ++i) {
        float t = static_cast<float>(i) / n, u = 1 - t;
        o.push_back({u * u * u * a.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * p.x,
                     u * u * u * a.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * p.y});
    }
}

// Arc de cercle, angles en radians dans le repère de l'écran (y vers le bas)
void arcTo(Line& o, float cx, float cy, float r, float a0, float a1, int n = 16)
{
    for (int i = 1; i <= n; ++i) {
        float a = a0 + (a1 - a0) * i / n;
        o.push_back({cx + r * cosf(a), cy + r * sinf(a)});
    }
}

Line quad(Pt a, Pt c, Pt b)
{
    Line o{a};
    quadTo(o, c, b);
    return o;
}

Line seg(Pt a, Pt b)
{
    return {a, b};
}

Line heart(float cx, float cy, float s)
{
    Line o{{cx, cy + 0.8f * s}};
    cubicTo(o, {cx - 1.3f * s, cy + 0.1f * s}, {cx - 0.7f * s, cy - 0.9f * s}, {cx, cy - 0.35f * s});
    cubicTo(o, {cx + 0.7f * s, cy - 0.9f * s}, {cx + 1.3f * s, cy + 0.1f * s}, {cx, cy + 0.8f * s});
    return o;
}

// « M x y q dx dy ex 0 t ex 0 … » : vagues régulières
Line wave(float x, float y, float dx, float dy, float step, int count)
{
    Line o{{x, y}};
    float sign = -1;  // première vague vers le haut, comme « q4 -5 »
    for (int i = 0; i < count; ++i) {
        float x0 = x + i * step;
        quadTo(o, {x0 + dx, y + sign * dy}, {x0 + step, y});
        sign = -sign;
    }
    return o;
}

/* ---------------------------------- Couleurs RGB565 ---------------------------------- */

inline uint16_t pack(uint32_t rgb)
{
    return static_cast<uint16_t>(((rgb >> 8) & 0xF800) | ((rgb >> 5) & 0x07E0) | ((rgb >> 3) & 0x001F));
}

inline uint16_t mix(uint16_t dst, uint32_t rgb, int a)  // a : 0..256
{
    int dr = (dst >> 11) & 0x1F, dg = (dst >> 5) & 0x3F, db = dst & 0x1F;
    int sr = (rgb >> 19) & 0x1F, sg = (rgb >> 10) & 0x3F, sb = (rgb >> 3) & 0x1F;
    dr += ((sr - dr) * a) >> 8;
    dg += ((sg - dg) * a) >> 8;
    db += ((sb - db) * a) >> 8;
    return static_cast<uint16_t>((dr << 11) | (dg << 5) | db);
}

}  // namespace

/* ------------------------------------- Tampons ------------------------------------- */

void* psram_alloc(size_t n)
{
    void* p = heap_caps_malloc(n, MALLOC_CAP_SPIRAM);
    if (!p) {
        abort();  // PSRAM pleine (8 Mo) : ne devrait pas arriver
    }
    return p;
}

void psram_free(void* p)
{
    heap_caps_free(p);
}

FaceRenderer::~FaceRenderer()
{
    heap_caps_free(_px);
    heap_caps_free(_base);
    heap_caps_free(_shown);
    heap_caps_free(_mask);
    heap_caps_free(_row);
}

bool FaceRenderer::begin()
{
    _px    = static_cast<uint16_t*>(heap_caps_malloc(W * H * sizeof(uint16_t), MALLOC_CAP_SPIRAM));
    _base  = static_cast<uint16_t*>(heap_caps_malloc(W * H * sizeof(uint16_t), MALLOC_CAP_SPIRAM));
    _shown = static_cast<uint16_t*>(heap_caps_calloc(W * H, sizeof(uint16_t), MALLOC_CAP_SPIRAM));
    _mask  = static_cast<uint8_t*>(heap_caps_calloc(W * H, 1, MALLOC_CAP_SPIRAM));
    _row   = static_cast<float*>(heap_caps_malloc((W + 1) * sizeof(float), MALLOC_CAP_SPIRAM));
    if (!_px || !_base || !_shown || !_mask || !_row) {
        ESP_LOGE(TAG, "PSRAM insuffisante pour le visage");
        return false;
    }
    return true;
}

FaceRenderer::Style FaceRenderer::style(Look look)
{
    // Mêmes valeurs que les styles de la maquette
    switch (look) {
        case Look::A:
            return {0x0B1A3A, 0xFFFFFF, 0x4FD1FF, 0xFF7A9A, 0xFF8A3D, 0x4FD1FF, 0xFF8FB5, 0x0B1A3A, 9, 1, false, false};
        case Look::D:
            return {0xFFFBF6, 0x14204A, 0xE9548E, 0xF8BCD6, 0xF0703C, 0x6CC8F5, 0xF48FB5, 0x14204A, 5, 1, true, false};
        case Look::E:
        default:
            return {0x0B1A3A, 0xFFFFFF, 0x4FD1FF, 0xFF8FB5, 0xFF8A3D, 0x4FD1FF, 0xFF8FB5, 0x0B1A3A, 5, 0.55f, true, true};
    }
}

/* ---------------------------------- Rastérisation ---------------------------------- */

void FaceRenderer::rasterize(const Line& poly)
{
    const size_t n = poly.size();
    if (n < 3) {
        return;
    }
    float minx = poly[0].x, maxx = minx, miny = poly[0].y, maxy = miny;
    for (const auto& p : poly) {
        minx = std::min(minx, p.x);
        maxx = std::max(maxx, p.x);
        miny = std::min(miny, p.y);
        maxy = std::max(maxy, p.y);
    }
    const int x0 = std::max(0, static_cast<int>(floorf(minx)));
    const int x1 = std::min(W - 1, static_cast<int>(ceilf(maxx)));
    const int y0 = std::max(0, static_cast<int>(floorf(miny)));
    const int y1 = std::min(H - 1, static_cast<int>(ceilf(maxy)));
    if (x0 > x1 || y0 > y1) {
        return;
    }
    _bx0 = std::min(_bx0, x0);
    _bx1 = std::max(_bx1, x1);
    _by0 = std::min(_by0, y0);
    _by1 = std::max(_by1, y1);

    auto& xs = _xs;
    auto span = [this](float a, float b) {
        a = std::max(0.f, std::min(static_cast<float>(W), a));
        b = std::max(0.f, std::min(static_cast<float>(W), b));
        if (b <= a) {
            return;
        }
        int ia = static_cast<int>(a), ib = static_cast<int>(b);
        if (ia == ib) {
            _row[ia] += b - a;
            return;
        }
        _row[ia] += (ia + 1) - a;
        for (int i = ia + 1; i < ib; ++i) {
            _row[i] += 1.f;
        }
        if (ib < W) {
            _row[ib] += b - ib;
        }
    };

    for (int y = y0; y <= y1; ++y) {
        std::fill(_row + x0, _row + x1 + 2, 0.f);
        for (int s = 0; s < 4; ++s) {
            const float sy = y + (s + 0.5f) / 4.f;
            xs.clear();
            for (size_t i = 0; i < n; ++i) {
                const Pt& a = poly[i];
                const Pt& b = poly[(i + 1) % n];
                if (a.y == b.y) {
                    continue;
                }
                const float ylo = std::min(a.y, b.y), yhi = std::max(a.y, b.y);
                if (sy < ylo || sy >= yhi) {
                    continue;
                }
                const float t = (sy - a.y) / (b.y - a.y);
                xs.push_back({a.x + t * (b.x - a.x), a.y < b.y ? 1 : -1});
            }
            std::sort(xs.begin(), xs.end(), [](const auto& p, const auto& q) { return p.first < q.first; });
            int wind = 0;
            float start = 0;
            for (const auto& c : xs) {
                const int prev = wind;
                wind += c.second;
                if (prev == 0 && wind != 0) {
                    start = c.first;
                } else if (prev != 0 && wind == 0) {
                    span(start, c.first);
                }
            }
        }
        uint8_t* m = _mask + y * W;
        for (int x = x0; x <= x1; ++x) {
            const float c = _row[x] * 0.25f;
            if (c > 0.f) {
                const int v = std::min(255, static_cast<int>(c * 255.f + 0.5f));
                if (v > m[x]) {
                    m[x] = static_cast<uint8_t>(v);
                }
            }
        }
    }
}

void FaceRenderer::blend(uint32_t rgb, float opacity)
{
    if (_bx1 < _bx0) {
        return;
    }
    const int op = static_cast<int>(opacity * 256.f + 0.5f);
    for (int y = _by0; y <= _by1; ++y) {
        uint8_t* m   = _mask + y * W;
        uint16_t* px = _px + y * W;
        for (int x = _bx0; x <= _bx1; ++x) {
            if (m[x]) {
                px[x] = mix(px[x], rgb, (m[x] * op + 127) / 255);  // 255 × 256 → 256 : couleur exacte
                m[x]  = 0;
            }
        }
    }
    _bx0 = W;
    _by0 = H;
    _bx1 = -1;
    _by1 = -1;
}

void FaceRenderer::fill(const Path& p, uint32_t rgb, float opacity)
{
    for (const auto& line : p) {
        rasterize(line);
    }
    blend(rgb, opacity);
}

void FaceRenderer::stroke(const Path& p, uint32_t rgb, float width, bool closed)
{
    const float r = width / 2.f;
    for (const auto& line : p) {
        const size_t n = line.size();
        if (n == 0) {
            continue;
        }
        const size_t segs = closed ? n : n - 1;
        if (segs == 0) {
            rasterize(ell(line[0].x, line[0].y, r, r));
        }
        for (size_t i = 0; i < segs; ++i) {
            // Capsule : segment épaissi aux bouts arrondis (les jonctions le sont aussi)
            const Pt a = line[i], b = line[(i + 1) % n];
            const float dx = b.x - a.x, dy = b.y - a.y, len = sqrtf(dx * dx + dy * dy);
            if (len < 1e-3f) {
                rasterize(ell(a.x, a.y, r, r));
                continue;
            }
            // Demi-cercles : direction n·cos(kπ/8) + u·sin(kπ/8), n normale, u sens du segment
            static float kc[9], ks[9];
            static bool table = false;
            if (!table) {
                for (int k = 0; k <= 8; ++k) {
                    kc[k] = cosf(PI * k / 8);
                    ks[k] = sinf(PI * k / 8);
                }
                table = true;
            }
            const float ux = dx / len, uy = dy / len, nx = -uy, ny = ux;
            auto& cap = _cap;
            cap.clear();
            for (int k = 0; k <= 8; ++k) {
                cap.push_back({b.x + r * (nx * kc[k] + ux * ks[k]), b.y + r * (ny * kc[k] + uy * ks[k])});
            }
            for (int k = 0; k <= 8; ++k) {
                cap.push_back({a.x - r * (nx * kc[k] + ux * ks[k]), a.y - r * (ny * kc[k] + uy * ks[k])});
            }
            rasterize(cap);
        }
    }
    blend(rgb, 1.f);
}

/* ------------------------------------- Le visage ------------------------------------ */

void FaceRenderer::eye(const Style& S, int side, int kind, const FaceState& st)
{
    static const int blinkable[] = {OPEN, WIDE, UP, ANGRY, SAD, HALF};
    if (std::find(std::begin(blinkable), std::end(blinkable), kind) != std::end(blinkable)) {
        if (st.blink == 1) {
            kind = HALF;
        } else if (st.blink == 2) {
            kind = SHUT;
        }
    }
    if (kind == NONE) {
        return;
    }
    const float cx = (side < 0 ? 112.f : 208.f) + st.gazeX;
    const float cy = (S.kawaii ? 100.f : 104.f) + st.gazeY;
    const uint32_t ink = S.ink;

    if (S.kawaii) {
        // Yeux ronds à double reflet ; arcs fins pour la joie et le sommeil
        switch (kind) {
            case HAPPY: {
                Line o{{cx - 14, cy + 10}, {cx - 14, cy - 2}};
                arcTo(o, cx, cy - 2, 14, PI, 2 * PI);
                o.push_back({cx + 14, cy + 10});
                return stroke({o}, ink, S.sw);
            }
            case SLEEP: {
                Line o{{cx - 14, cy - 10}, {cx - 14, cy + 2}};
                arcTo(o, cx, cy + 2, 14, PI, 0);
                o.push_back({cx + 14, cy - 10});
                return stroke({o}, ink, S.sw);
            }
            case SQUINT:
                return stroke({side < 0 ? Line{{cx - 14, cy - 10}, {cx + 10, cy}, {cx - 14, cy + 10}}
                                        : Line{{cx + 14, cy - 10}, {cx - 10, cy}, {cx + 14, cy + 10}}},
                              ink, S.sw);
            case XX:
                return stroke({seg({cx - 12, cy - 12}, {cx + 12, cy + 12}), seg({cx + 12, cy - 12}, {cx - 12, cy + 12})},
                              ink, S.sw);
            case HEART:
                return fill({heart(cx, cy, 19)}, S.acc);
            case SHUT:
                return stroke({quad({cx - 16, cy}, {cx, cy + 6}, {cx + 16, cy})}, ink, S.sw);
            default:
                break;
        }
        const float r  = kind == WIDE ? 21.f : 17.f;
        const float dy = kind == UP ? -6.f : 0.f;
        fill({ell(cx, cy + dy, r, r)}, ink);
        if (S.hasIris) {
            fill({ell(cx, cy + dy, r - 4, r - 4)}, S.iris);
        }
        fill({ell(cx + r * 0.32f, cy + dy - r * 0.32f, r * 0.36f, r * 0.36f)}, 0xFFFFFF);
        fill({ell(cx - r * 0.35f, cy + dy + r * 0.38f, r * 0.15f, r * 0.15f)}, 0xFFFFFF);
        if (kind == HALF) {
            fill({rect(cx - r - 2, cy - r - 2, 2 * r + 4, r + 4)}, S.bg);
            stroke({seg({cx - r, cy + 1}, {cx + r, cy + 1})}, ink, 4);
        }
        // Sourcils : en V pour la colère, relevés au milieu pour la tristesse (la maquette les
        // avait inversés)
        if (kind == ANGRY) {
            stroke({seg({cx - side * 20, cy - 21}, {cx + side * 12, cy - 30})}, ink, 4);
        }
        if (kind == SAD) {
            stroke({seg({cx - side * 20, cy - 30}, {cx + side * 12, cy - 21})}, ink, 4);
        }
        return;
    }

    // Visière (A)
    switch (kind) {
        case SHUT:
            return stroke({seg({cx - 20, cy}, {cx + 20, cy})}, ink, S.sw);
        case SLEEP:
            return stroke({quad({cx - 22, cy}, {cx, cy + 16}, {cx + 22, cy})}, ink, S.sw);
        case HAPPY:
            return stroke({quad({cx - 22, cy + 8}, {cx, cy - 18}, {cx + 22, cy + 8})}, ink, S.sw);
        case SQUINT:
            return stroke({side < 0 ? Line{{cx - 14, cy - 15}, {cx + 12, cy}, {cx - 14, cy + 15}}
                                    : Line{{cx + 14, cy - 15}, {cx - 12, cy}, {cx + 14, cy + 15}}},
                          ink, S.sw);
        case XX:
            return stroke({seg({cx - 15, cy - 15}, {cx + 15, cy + 15}), seg({cx + 15, cy - 15}, {cx - 15, cy + 15})},
                          ink, S.sw);
        case HEART:
            return fill({heart(cx, cy, 22)}, S.pink);
        default:
            break;
    }
    float e[3] = {13, 21, 0};
    switch (kind) {
        case WIDE: e[0] = 18; e[1] = 27; e[2] = 0; break;
        case UP: e[0] = 13; e[1] = 19; e[2] = -10; break;
        case HALF: e[0] = 15; e[1] = 8; e[2] = 7; break;
        case ANGRY: e[0] = 13; e[1] = 18; e[2] = 3; break;
        case SAD: e[0] = 12; e[1] = 18; e[2] = 3; break;
        default: break;
    }
    fill({ell(cx, cy + e[2], e[0], e[1])}, ink);
    if (kind == WIDE) {
        fill({ell(cx + 6, cy - 10, 5, 5)}, S.bg);
    }
    if (kind == ANGRY) {  // en V (inversé par rapport à la maquette, voir plus haut)
        stroke({seg({cx - side * 22, cy - 24}, {cx + side * 16, cy - 38})}, ink, S.sw);
    }
    if (kind == SAD) {
        stroke({seg({cx - side * 22, cy - 38}, {cx + side * 16, cy - 24})}, ink, S.sw);
    }
}

void FaceRenderer::mouth(const Style& S, int kind, const FaceState& st)
{
    if (st.talk >= 0 && kind != M_NONE) {
        kind = TALK;
    }
    const float t      = st.talk;
    const float my     = st.mouthY;
    const uint32_t ink = S.ink;
    auto P = [my](float x, float y) { return Pt{x, y + my}; };

    if (S.kawaii) {
        const float w = 4;
        switch (kind) {
            case SMALL: return stroke({quad(P(152, 128), P(160, 134), P(168, 128))}, ink, w);
            case SMILE: return stroke({quad(P(146, 126), P(160, 138), P(174, 126))}, ink, w);
            case BIG: {
                Line o{P(146, 122), P(174, 122)};
                quadTo(o, P(174, 142), P(160, 142));
                quadTo(o, P(146, 142), P(146, 122));
                fill({o}, ink);
                return fill({ell(160, 136 + my, 7, 4)}, S.tongue);
            }
            case FLAT: return stroke({seg(P(150, 130), P(170, 130))}, ink, w);
            case FROWN: return stroke({quad(P(148, 134), P(160, 124), P(172, 134))}, ink, w);
            case O:
                fill({ell(160, 132 + my, 7, 9)}, ink);
                return fill({ell(160, 135 + my, 4, 4)}, S.tongue);
            case WAVY: return stroke({wave(144, 130 + my, 4, 5, 8, 4)}, ink, w);
            case SMIRK: return stroke({quad(P(150, 131), P(162, 135), P(172, 124))}, ink, w);
            case CAT: {
                Line o = quad(P(146, 126), P(153, 136), P(160, 128));
                quadTo(o, P(167, 136), P(174, 126));
                return stroke({o}, ink, w);
            }
            case TALK:
                fill({ell(160, 130 + my, 6 + t * 1.5f, 2 + t * 2.5f)}, ink);
                if (t >= 2) {
                    fill({ell(160, 132 + t + my, 3 + t, 2 + t * 0.5f)}, S.tongue);
                }
                return;
            default: return;
        }
    }

    switch (kind) {
        case SMALL: return stroke({quad(P(148, 168), P(160, 177), P(172, 168))}, ink, S.sw);
        case SMILE: return stroke({quad(P(134, 164), P(160, 186), P(186, 164))}, ink, S.sw);
        case BIG: return fill({quad(P(130, 158), P(160, 204), P(190, 158))}, ink);
        case FLAT: return stroke({seg(P(146, 170), P(174, 170))}, ink, S.sw);
        case FROWN: return stroke({quad(P(136, 180), P(160, 160), P(184, 180))}, ink, S.sw);
        case O: return stroke({ell(160, 172 + my, 10, 13)}, ink, S.sw, true);
        case WAVY: return stroke({wave(132, 174 + my, 7, 9, 14, 4)}, ink, S.sw);
        case SMIRK: return stroke({quad(P(140, 174), P(164, 178), P(182, 162))}, ink, S.sw);
        case CAT: {
            Line o = quad(P(136, 166), P(148, 182), P(160, 168));
            quadTo(o, P(172, 182), P(184, 166));
            return stroke({o}, ink, S.sw);
        }
        case TALK: return fill({ell(160, 170 + my, 13 + t * 2, 3 + t * 4)}, ink);
        default: return;
    }
}

void FaceRenderer::shades(const Style& S, float drop)
{
    // Lunettes pixel « Deal with it » : elles tombent du haut quand drop passe de 0 à 1
    const float u  = 8;
    const float x0 = 56;
    const float y0 = 84 - roundf((1 - drop) * 150);
    static const int rows[5][2][2] = {
        {{0, 26}, {0, 0}}, {{1, 25}, {0, 0}}, {{2, 12}, {14, 24}}, {{3, 11}, {15, 23}}, {{4, 10}, {16, 22}},
    };
    static const int shine[5][2] = {{1, 3}, {1, 5}, {2, 4}, {2, 6}, {3, 5}};
    Path black, rim, white;
    for (int j = 0; j < 5; ++j) {
        for (int k = 0; k < 2; ++k) {
            const int a = rows[j][k][0], b = rows[j][k][1];
            if (b <= a) {
                continue;
            }
            black.push_back(rect(x0 + a * u, y0 + j * u, (b - a) * u, u));
            rim.push_back(rect(x0 + a * u - 3, y0 + j * u - 3, (b - a) * u + 6, u + 6));
        }
    }
    for (const auto& p : shine) {
        white.push_back(rect(x0 + p[1] * u, y0 + p[0] * u, u, u));
        white.push_back(rect(x0 + (p[1] + 12) * u, y0 + p[0] * u, u, u));
    }
    fill(rim, S.ink);
    fill(black, 0x050505);
    fill(white, 0xFFFFFF);
}

FaceRenderer::Rect FaceRenderer::present(const Rect& zone)
{
    Rect r{W, H, -1, -1};
    if (!_px || zone.empty()) {
        return r;
    }
    const int zx0 = std::max(0, zone.x0), zx1 = std::min(W - 1, zone.x1);
    const int zy0 = std::max(0, zone.y0), zy1 = std::min(H - 1, zone.y1);
    for (int y = zy0; y <= zy1; ++y) {
        const uint16_t* a = _px + y * W;
        const uint16_t* b = _shown + y * W;
        if (memcmp(a + zx0, b + zx0, (zx1 - zx0 + 1) * sizeof(uint16_t)) == 0) {
            continue;
        }
        int x0 = zx0, x1 = zx1;
        while (a[x0] == b[x0]) {
            ++x0;
        }
        while (a[x1] == b[x1]) {
            --x1;
        }
        r.x0 = std::min(r.x0, x0);
        r.x1 = std::max(r.x1, x1);
        r.y0 = std::min(r.y0, y);
        r.y1 = y;
    }
    if (!r.empty()) {
        const int n = (r.x1 - r.x0 + 1) * sizeof(uint16_t);
        for (int y = r.y0; y <= r.y1; ++y) {
            memcpy(_shown + y * W + r.x0, _px + y * W + r.x0, n);
        }
    }
    return r;
}

// Zones redessinées à part (yeux au clignement, bouche pendant la parole). Elles ne se
// chevauchent pas et contiennent toutes les formes possibles de leur trait.
FaceRenderer::Rect FaceRenderer::mouthZone(bool kawaii)
{
    return kawaii ? Rect{140, 102, 180, 158} : Rect{126, 146, 194, 218};
}

FaceRenderer::Rect FaceRenderer::eyeZone(bool kawaii, int side)
{
    if (kawaii) {
        return side < 0 ? Rect{86, 64, 138, 134} : Rect{182, 64, 234, 134};
    }
    return side < 0 ? Rect{82, 58, 142, 134} : Rect{178, 58, 238, 134};
}

void FaceRenderer::restore(const Rect& z)
{
    const int n = (z.x1 - z.x0 + 1) * sizeof(uint16_t);
    for (int y = z.y0; y <= z.y1; ++y) {
        memcpy(_px + y * W + z.x0, _base + y * W + z.x0, n);
    }
}

// Fond : couleur, joues et accessoires, sans les yeux ni la bouche
void FaceRenderer::drawBase(const FaceState& st, bool withShades)
{
    const Style S   = style(st.look);
    const ExprDef E = kExpr[static_cast<int>(st.expr)];

    const uint16_t bg = pack(S.bg);
    for (int i = 0; i < W * H; ++i) {
        _px[i] = bg;
    }
    if (S.kawaii) {
        fill({ell(96, 124, 21, 18), ell(224, 124, 21, 18)}, S.pink, S.cheek);
    }
    if ((E.x & X_BLUSH) && !S.kawaii) {
        fill({ell(80, 140, 17, 9), ell(240, 140, 17, 9)}, S.pink, 0.75f);
    }
    if (E.x & X_TEARS) {
        Line l{{100, 132}};
        quadTo(l, {110, 146}, {100, 154});
        quadTo(l, {90, 146}, {100, 132});
        Line r{{220, 132}};
        quadTo(r, {230, 146}, {220, 154});
        quadTo(r, {210, 146}, {220, 132});
        fill({l, r}, S.water);
    }
    if (E.x & X_ZZZ) {
        stroke({Line{{228, 34}, {244, 34}, {228, 50}, {244, 50}}, Line{{256, 18}, {267, 18}, {256, 29}, {267, 29}}},
               S.acc, 4);
    }
    if (E.x & X_QUESTION) {
        Line q{{250, 46}};
        quadTo(q, {250, 30}, {264, 30});
        quadTo(q, {278, 30}, {278, 44});
        quadTo(q, {278, 54}, {266, 58});
        q.push_back({265, 66});
        stroke({q}, S.acc, 7);
        fill({ell(265, 80, 4, 4)}, S.acc);
    }
    if ((E.x & X_SHADES) && withShades) {
        shades(S, st.drop);
    }
}

FaceRenderer::Dirty FaceRenderer::render(const FaceState& in)
{
    Dirty d;
    if (!_px) {
        return d;
    }
    FaceState st = in;
    st.gazeX = st.gazeY = st.mouthY = 0;  // traits fixes : les zones ci-dessus le supposent
    const Style S   = style(st.look);
    const ExprDef E = kExpr[static_cast<int>(st.expr)];
    // Lunettes « Deal with it » : par-dessus les yeux et le haut de la bouche. Leur chute (3 étapes)
    // redessine tout ; une fois posées, seule la bouche change et les lunettes sont remises dessus
    // (rectangles alignés sur les pixels : les redessiner ne change rien ailleurs).
    const bool cool = (E.x & X_SHADES) != 0;

    if (!_baseValid || st.look != _bs.look || st.expr != _bs.expr || st.eyesOn != _bs.eyesOn ||
        (cool && st.drop != _bs.drop)) {
        // Nouvelle expression ou nouveau style : fond complet, puis yeux et bouche (rare, coûteux)
        uint16_t* work = _px;
        _px            = _base;
        drawBase(st, false);
        _px = work;
        memcpy(_px, _base, W * H * sizeof(uint16_t));
        if (st.eyesOn) {
            eye(S, -1, E.l, st);
            eye(S, 1, E.r, st);
        }
        if (st.mouthOn) {
            mouth(S, E.m, st);
        }
        if (cool) {
            shades(S, st.drop);
        }
        _bs        = st;
        _baseValid = true;
        d.add(Rect{0, 0, W - 1, H - 1});
        return d;
    }
    if (st.blink != _bs.blink && !cool) {
        // Clignement : les deux zones des yeux seulement (cachés sous les lunettes en mode cool)
        for (int side = -1; side <= 1; side += 2) {
            const Rect z = eyeZone(S.kawaii, side);
            restore(z);
            if (st.eyesOn) {
                eye(S, side, side < 0 ? E.l : E.r, st);
            }
            d.add(z);
        }
    }
    if (st.talk != _bs.talk || st.mouthOn != _bs.mouthOn) {
        // Parole : la zone de la bouche seulement
        const Rect z = mouthZone(S.kawaii);
        restore(z);
        if (st.mouthOn) {
            mouth(S, E.m, st);
        }
        if (cool) {
            shades(S, st.drop);
        }
        d.add(z);
    }
    _bs = st;
    return d;
}

}  // namespace billy
