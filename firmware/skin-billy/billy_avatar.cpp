/*
 * Skin « Billy » : branchement du visage sur l'avatar StackChan.
 */
#include <algorithm>
#include <atomic>
#include <string>
#include "billy.h"
#include <settings.h>

#include <cmath>
#include <cstring>
#include <ctime>
#include <esp_log.h>
#include <esp_timer.h>
#include <hal/hal.h>

using namespace uitk::lvgl_cpp;
using namespace stackchan::avatar;

namespace {

const char* TAG = "BillyAvatar";

// Style choisi à distance : -2 pas encore lu en NVS, -1 thème du jour, 0 à 2 pour A, D, E
std::atomic<int> s_choix{-2};
std::atomic<char> s_affiche{'?'};
const billy::Look kLooks[] = {billy::Look::A, billy::Look::D, billy::Look::E};

int index_style(const std::string& style)
{
    if (style == "A" || style == "a") return 0;
    if (style == "D" || style == "d") return 1;
    if (style == "E" || style == "e") return 2;
    if (style == "auto") return -1;
    return -3;
}

int choix()
{
    int c = s_choix.load();
    if (c == -2) {
        Settings settings("billy", false);
        c = index_style(settings.GetString("style", "auto"));
        if (c < -1) {
            c = -1;
        }
        s_choix.store(c);
    }
    return c;
}

constexpr uint32_t kTalkHoldMs  = 500;   // la bouche reste « en parole » ce temps après la dernière ouverture
constexpr uint32_t kBlinkHalfMs = 60;    // mi-clos au début et à la fin d'un clignement
constexpr uint32_t kDropMs      = 900;   // descente des lunettes « Deal with it » (3 étapes)
constexpr uint32_t kMinFrameMs  = 50;    // au plus 20 images par seconde : la voix (opus_codec, priorité 2)
                                         // passe après LVGL (priorité 3)

// ToUint32 / ToInt32 de JavaScript, pour tirer le même thème du jour que la maquette
uint32_t toU32(double x)
{
    double m = std::fmod(std::trunc(x), 4294967296.0);
    if (m < 0) {
        m += 4294967296.0;
    }
    return static_cast<uint32_t>(m);
}

int32_t toI32(double x)
{
    return static_cast<int32_t>(toU32(x));
}

billy::Expr fromEmotion(Emotion e)
{
    switch (e) {
        case Emotion::Happy: return billy::Expr::Content;
        case Emotion::Angry: return billy::Expr::Colere;
        case Emotion::Sad: return billy::Expr::Triste;
        case Emotion::Doubt: return billy::Expr::Doute;
        case Emotion::Sleepy: return billy::Expr::Endormi;
        case Emotion::Neutral:
        default: return billy::Expr::Neutre;
    }
}

}  // namespace

/* ------------------------------------- Traits ------------------------------------- */

void BillyFeature::setPosition(const uitk::Vector2i& position)
{
    Element::setPosition(position);
    _owner->markDirty();
}

void BillyFeature::setWeight(int weight)
{
    Feature::setWeight(weight);
    if (weight > 0) {
        _last_active_ms = GetHAL().millis();
    }
    _owner->markDirty();
}

void BillyFeature::setEmotion(const Emotion& emotion)
{
    // Les yeux restent grands ouverts : la forme vient de l'expression, et le clignement
    // (BlinkModifier) repart de ce poids après chaque changement d'émotion.
    if (!getIgnoreEmotion() && this != &_owner->mouth()) {
        Feature::setWeight(100);
    }
    _owner->markDirty();
}

void BillyFeature::setVisible(bool visible)
{
    Element::setVisible(visible);
    _owner->markDirty();
}

/* ------------------------------------- Avatar ------------------------------------- */

BillyAvatar::~BillyAvatar()
{
    _key_elements.speechBubble.reset();
    _canvas = nullptr;
    _pannel.reset();
}

void BillyAvatar::init(lv_obj_t* parent, const lv_font_t* font)
{
    _pannel = std::make_unique<Container>(parent);
    _pannel->align(LV_ALIGN_CENTER, 0, 0);
    _pannel->setSize(billy::FaceRenderer::W, billy::FaceRenderer::H);
    _pannel->setRadius(0);
    _pannel->setBorderWidth(0);
    _pannel->setPadding(0, 0, 0, 0);
    _pannel->setBgColor(lv_color_hex(0x0B1A3A));
    _pannel->removeFlag(LV_OBJ_FLAG_SCROLLABLE);

    _ready = _renderer.begin();
    if (_ready) {
        _canvas = lv_canvas_create(_pannel->get());
        lv_canvas_set_buffer(_canvas, _renderer.pixels(), billy::FaceRenderer::W, billy::FaceRenderer::H,
                             LV_COLOR_FORMAT_RGB565);
        lv_obj_align(_canvas, LV_ALIGN_CENTER, 0, 0);
        lv_obj_remove_flag(_canvas, LV_OBJ_FLAG_CLICKABLE);
    }

    _key_elements.leftEye  = std::make_unique<BillyFeature>(this);
    _key_elements.rightEye = std::make_unique<BillyFeature>(this);
    _key_elements.mouth    = std::make_unique<BillyFeature>(this);
    _key_elements.leftEye->setWeight(100);
    _key_elements.rightEye->setWeight(100);
    _key_elements.speechBubble =
        std::make_unique<DefaultSpeechBubble>(_pannel->get(), lv_color_white(), lv_color_black(), font);

    _force_vu = choix();
    _look     = _force_vu >= 0 ? kLooks[_force_vu] : lookOfDay();
    s_affiche.store("ADE"[static_cast<int>(_look)]);
    ESP_LOGI(TAG, "Visage de Billy prêt (style %c%s)", "ADE"[static_cast<int>(_look)],
             _force_vu >= 0 ? ", choisi à distance" : "");
    redraw();
}

void BillyAvatar::setEmotion(const Emotion& emotion)
{
    Avatar::setEmotion(emotion);
    _expr = fromEmotion(emotion);
    markDirty();
}

void BillyAvatar::setExpressionName(const char* name)
{
    if (!name) {
        return;
    }
    static const struct {
        const char* name;
        billy::Expr expr;
    } table[] = {
        {"neutral", billy::Expr::Neutre},     {"happy", billy::Expr::Content},      {"relaxed", billy::Expr::Content},
        {"delicious", billy::Expr::Content},  {"confident", billy::Expr::Content},  {"laughing", billy::Expr::Rire},
        {"funny", billy::Expr::Rire},         {"sad", billy::Expr::Triste},         {"crying", billy::Expr::Pleure},
        {"angry", billy::Expr::Colere},       {"doubtful", billy::Expr::Doute},     {"confused", billy::Expr::Doute},
        {"surprised", billy::Expr::Surpris},  {"shocked", billy::Expr::Surpris},    {"thinking", billy::Expr::Reflechit},
        {"loving", billy::Expr::Amoureux},    {"kissy", billy::Expr::Amoureux},     {"winking", billy::Expr::Clin},
        {"silly", billy::Expr::Clin},         {"embarrassed", billy::Expr::Timide}, {"shy", billy::Expr::Timide},
        {"dizzy", billy::Expr::Etourdi},      {"sleepy", billy::Expr::Endormi},     {"cool", billy::Expr::Cool},
    };
    for (const auto& e : table) {
        if (strcmp(name, e.name) == 0) {
            if (e.expr == billy::Expr::Cool && _expr != billy::Expr::Cool) {
                _drop_start_ms = GetHAL().millis();
            }
            _expr = e.expr;
            markDirty();
            return;
        }
    }
}

billy::Look BillyAvatar::lookOfDay()
{
    // Même tirage que le bouton « Thème du jour » de la maquette : un style fixe par date
    time_t now = time(nullptr);
    struct tm tm;
    localtime_r(&now, &tm);
    if (tm.tm_year + 1900 < 2025) {
        return _look;  // heure pas encore reçue du serveur
    }
    double h = (tm.tm_year + 1900) * 10000.0 + (tm.tm_mon + 1) * 100.0 + tm.tm_mday;
    for (int i = 0; i < 2; ++i) {
        const int32_t x = static_cast<int32_t>(toU32(h) >> 16) ^ toI32(h);
        h = static_cast<double>(x) * 73244475.0;  // 0x45d9f3b, en flottant comme en JavaScript
    }
    const int32_t r = static_cast<int32_t>(toU32(h) >> 16) ^ toI32(h);
    static const billy::Look looks[] = {billy::Look::A, billy::Look::D, billy::Look::E};
    return looks[static_cast<int64_t>(std::llabs(static_cast<long long>(r))) % 3];
}

void BillyAvatar::update()
{
    Avatar::update();
    redraw();
}

void BillyAvatar::redraw()
{
    if (!_ready || !_key_elements.leftEye) {
        return;
    }
    const uint32_t now = GetHAL().millis();

    const int choisi = choix();
    if (choisi != _force_vu || now - _look_check_ms > 60000 || _look_check_ms == 0) {
        _force_vu              = choisi;
        _look_check_ms         = now;
        const billy::Look look = choisi >= 0 ? kLooks[choisi] : lookOfDay();
        if (look != _look) {
            ESP_LOGI(TAG, "%s : style %c", choisi >= 0 ? "Style choisi" : "Thème du jour", "ADE"[static_cast<int>(look)]);
            _look = look;
            markDirty();
        }
        s_affiche.store("ADE"[static_cast<int>(_look)]);
    }

    auto& left  = *_key_elements.leftEye;
    auto& mouth = static_cast<BillyFeature&>(*_key_elements.mouth);

    // Clignement : BlinkModifier ferme les yeux (poids 25) pendant 200 ms
    const int w = left.getWeight();
    if (w <= 40 && _last_eye_weight > 40) {
        _blink_ms = now;
    }
    if (w > 40 && _last_eye_weight <= 40) {
        _reopen_ms = now;
    }
    _last_eye_weight = w;

    billy::FaceState st;
    st.look  = _look;
    st.expr  = _expr;
    st.blink = w <= 40 ? (now - _blink_ms < kBlinkHalfMs ? 1 : 2) : (w <= 80 || now - _reopen_ms < kBlinkHalfMs ? 1 : 0);
    st.talk  = (now - mouth.lastActiveMs() < kTalkHoldMs) ? roundf(mouth.getWeight() / 12.5f) / 2.f : -1.f;
    // Chute des lunettes en 3 étapes (chacune redessine tout le visage, environ 60 ms sur Billy)
    st.drop = 1.f;
    if (_expr == billy::Expr::Cool) {
        const uint32_t pas = (now - _drop_start_ms) / (kDropMs / 3);
        st.drop            = pas >= 2 ? 1.f : (pas == 1 ? 0.7f : 0.35f);
    }
    // Pas de micro-déplacements (respiration, regard) : chacun obligeait à tout redessiner, et la
    // tête de Billy bouge déjà. Les yeux et la bouche restent à leur place dans le visage.
    st.eyesOn  = left.getVisible();
    st.mouthOn = mouth.getVisible();

    if ((!_dirty && st == _drawn) || now - _last_frame_ms < kMinFrameMs) {
        return;
    }
    _last_frame_ms = now;
    const int64_t t0 = esp_timer_get_time();
    const billy::FaceRenderer::Dirty zones = _renderer.render(st);
    _drawn = st;
    _dirty = false;
    lv_area_t c;
    lv_obj_get_coords(_canvas, &c);
    for (int i = 0; i < zones.n; ++i) {
        const billy::FaceRenderer::Rect r = _renderer.present(zones.r[i]);
        if (r.empty()) {
            continue;
        }
        // Seul le rectangle modifié part vers l'écran
        lv_area_t a = {static_cast<int32_t>(c.x1 + r.x0), static_cast<int32_t>(c.y1 + r.y0),
                       static_cast<int32_t>(c.x1 + r.x1), static_cast<int32_t>(c.y1 + r.y1)};
        lv_obj_invalidate_area(_canvas, &a);
        _stat_px += (r.x1 - r.x0 + 1) * (r.y1 - r.y0 + 1);
    }
    const int64_t duree = esp_timer_get_time() - t0;
    _stat_us += duree;
    _stat_max_us = std::max(_stat_max_us, duree);
    if (zones.n > 0 && zones.r[0].x0 == 0 && zones.r[0].y0 == 0 && zones.r[0].x1 == billy::FaceRenderer::W - 1 &&
        zones.r[0].y1 == billy::FaceRenderer::H - 1) {
        ++_stat_complets;
    }
    if (++_stat_frames == 100) {
        ESP_LOGI(TAG, "100 images : dessin %lld µs en moyenne, %lld µs au pire, %d en entier, %lld px envoyés en moyenne",
                 _stat_us / 100, _stat_max_us, _stat_complets, _stat_px / 100);
        _stat_frames   = 0;
        _stat_us       = 0;
        _stat_px       = 0;
        _stat_max_us   = 0;
        _stat_complets = 0;
    }
}

bool BillyAvatar::setStyle(const std::string& style)
{
    const int c = index_style(style);
    if (c < -1) {
        return false;
    }
    {
        Settings settings("billy", true);
        settings.SetString("style", c < 0 ? "auto" : std::string(1, "ADE"[c]));
    }
    s_choix.store(c);
    ESP_LOGI(TAG, "Style demandé à distance : %s", c < 0 ? "auto (thème du jour)" : style.c_str());
    return true;
}

std::string BillyAvatar::styleJson()
{
    const int c = choix();
    std::string json = "{\"style\": \"";
    json += s_affiche.load();
    json += "\", \"choix\": \"";
    json += c < 0 ? std::string("auto") : std::string(1, "ADE"[c]);
    json += "\"}";
    return json;
}
