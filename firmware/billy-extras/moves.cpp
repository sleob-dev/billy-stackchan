/*
 * self.robot.play_moves : voir moves.h.
 */
#include "moves.h"
#include "../stackchan.h"
#include <ArduinoJson.hpp>
#include <algorithm>
#include <cstdio>
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <hal/hal.h>
#include <vector>

namespace {

const char* TAG          = "BillyMoves";
constexpr size_t kMaxPas = 300;
constexpr uint32_t kMaxT = 600000;  // 10 minutes

// Tout en PSRAM : la RAM interne de Billy est à bout
struct PsramJson : ArduinoJson::Allocator {
    void* allocate(size_t n) override
    {
        return heap_caps_malloc(n, MALLOC_CAP_SPIRAM);
    }
    void deallocate(void* p) override
    {
        heap_caps_free(p);
    }
    void* reallocate(void* p, size_t n) override
    {
        return heap_caps_realloc(p, n, MALLOC_CAP_SPIRAM);
    }
};

template <class T>
struct PsramAlloc {
    using value_type = T;
    PsramAlloc() = default;
    template <class U>
    PsramAlloc(const PsramAlloc<U>&)
    {
    }
    T* allocate(size_t n)
    {
        void* p = heap_caps_malloc(n * sizeof(T), MALLOC_CAP_SPIRAM);
        if (!p) {
            abort();
        }
        return static_cast<T*>(p);
    }
    void deallocate(T* p, size_t)
    {
        heap_caps_free(p);
    }
    template <class U>
    bool operator==(const PsramAlloc<U>&) const
    {
        return true;
    }
    template <class U>
    bool operator!=(const PsramAlloc<U>&) const
    {
        return false;
    }
};

struct Pas {
    uint32_t t_ms;
    int16_t yaw, pitch;  // degrés ; INT16_MIN : pas de mouvement
    uint16_t speed;
    int16_t r, g, b;     // -1 : pas de changement de LED
};
using Pas_t = std::vector<Pas, PsramAlloc<Pas>>;

class MovePlayerModifier;
MovePlayerModifier* g_actif = nullptr;  // lecteur vivant (son numéro peut être réattribué après lui)
int s_lecteur               = -1;       // numéro du lecteur vivant dans StackChan

class MovePlayerModifier : public stackchan::Modifier {
public:
    explicit MovePlayerModifier(Pas_t pas) : _pas(std::move(pas))
    {
        g_actif = this;
    }
    ~MovePlayerModifier() override
    {
        if (g_actif == this) {
            g_actif   = nullptr;
            s_lecteur = -1;
        }
    }
    void ajouter(const Pas_t& pas)
    {
        _pas.insert(_pas.end(), pas.begin(), pas.end());
        std::stable_sort(_pas.begin() + _i, _pas.end(), [](const Pas& a, const Pas& b) { return a.t_ms < b.t_ms; });
    }
    size_t taille() const
    {
        return _pas.size();
    }
    void _update(stackchan::Modifiable& stackchan) override
    {
        const uint32_t now = GetHAL().millis();
        if (!_lance) {
            _lance = true;
            _t0    = now;
        }
        while (_i < _pas.size() && now - _t0 >= _pas[_i].t_ms) {
            const Pas& p = _pas[_i++];
            if (p.pitch != INT16_MIN) {
                stackchan.motion().pitchServo().moveWithSpeed(p.pitch * 10, p.speed);
            }
            if (p.yaw != INT16_MIN) {
                stackchan.motion().yawServo().moveWithSpeed(p.yaw * 10, p.speed);
            }
            if (p.r >= 0) {
                stackchan.leftNeonLight().setColor(p.r, p.g, p.b);
                stackchan.rightNeonLight().setColor(p.r, p.g, p.b);
            }
        }
        if (_i >= _pas.size()) {
            ESP_LOGI(TAG, "Suite terminée (%u pas)", static_cast<unsigned>(_pas.size()));
            requestDestroy();
        }
    }

private:
    Pas_t _pas;
    size_t _i     = 0;
    bool _lance   = false;
    uint32_t _t0  = 0;
};

std::string erreur(const char* msg)
{
    ESP_LOGW(TAG, "play_moves refusé : %s", msg);
    return std::string(R"({"ok": false, "error": ")") + msg + "\"}";
}

int borne(int v, int lo, int hi)
{
    return std::max(lo, std::min(hi, v));
}

}  // namespace

std::string billy::play_moves(const std::string& moves, bool append)
{
    PsramJson alloc;
    ArduinoJson::JsonDocument doc(&alloc);
    if (ArduinoJson::deserializeJson(doc, moves)) {
        return erreur("moves n'est pas un tableau JSON valide");
    }
    if (!doc.is<ArduinoJson::JsonArrayConst>()) {
        return erreur("moves doit être un tableau JSON");
    }
    auto tableau = doc.as<ArduinoJson::JsonArrayConst>();

    auto& sc = GetStackChan();
    MovePlayerModifier* lecteur = g_actif;  // nul si aucune suite en cours
    if (lecteur && lecteur->isDestroyRequested()) {
        lecteur = nullptr;  // suite finie, pas encore retirée par StackChan
    }
    if (tableau.size() == 0) {
        if (lecteur) {
            sc.removeModifier(s_lecteur);
            s_lecteur = -1;
        }
        ESP_LOGI(TAG, "Suite arrêtée");
        return R"({"ok": true, "stopped": true})";
    }
    const size_t deja = (append && lecteur) ? lecteur->taille() : 0;
    if (deja + tableau.size() > kMaxPas) {
        return erreur("trop de pas (300 au plus)");
    }

    Pas_t pas;
    pas.reserve(tableau.size());
    uint32_t fin = 0;
    for (auto o : tableau) {
        if (!o.is<ArduinoJson::JsonObjectConst>() || !o["t_ms"].is<uint32_t>()) {
            return erreur("chaque pas doit être un objet avec t_ms");
        }
        Pas p;
        p.t_ms  = std::min<uint32_t>(o["t_ms"].as<uint32_t>(), kMaxT);
        p.yaw   = o["yaw"].is<int>() ? borne(o["yaw"].as<int>(), -128, 128) : INT16_MIN;
        p.pitch = o["pitch"].is<int>() ? borne(o["pitch"].as<int>(), 0, 90) : INT16_MIN;
        p.speed = borne(o["speed"] | 300, 100, 1000);
        const bool led = o["r"].is<int>() || o["g"].is<int>() || o["b"].is<int>();
        p.r = led ? borne(o["r"] | 0, 0, 168) : -1;
        p.g = led ? borne(o["g"] | 0, 0, 168) : -1;
        p.b = led ? borne(o["b"] | 0, 0, 168) : -1;
        pas.push_back(p);
        fin = std::max(fin, p.t_ms);
    }
    std::stable_sort(pas.begin(), pas.end(), [](const Pas& a, const Pas& b) { return a.t_ms < b.t_ms; });

    if (append && lecteur) {
        lecteur->ajouter(pas);
    } else {
        if (lecteur) {
            sc.removeModifier(s_lecteur);
        }
        s_lecteur = sc.addModifier(std::make_unique<MovePlayerModifier>(std::move(pas)));
    }
    const size_t total = deja + tableau.size();
    ESP_LOGI(TAG, "Suite de %u pas%s, dernier à %lu ms", static_cast<unsigned>(total), append ? " (complétée)" : "",
             static_cast<unsigned long>(fin));
    char rep[96];
    snprintf(rep, sizeof rep, R"({"ok": true, "steps": %u, "last_t_ms": %lu})", static_cast<unsigned>(total),
             static_cast<unsigned long>(fin));
    return rep;
}
