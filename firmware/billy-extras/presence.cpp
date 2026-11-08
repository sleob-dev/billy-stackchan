/*
 * « Il te regarde » : voir presence.h.
 */
#include <algorithm>
#include "presence.h"
#include "regard.h"
#include "../avatar/skins/billy/billy.h"
#include <application.h>
#include <ctime>
#include <sdkconfig.h>
#include <settings.h>
#include <string>
#include <esp_log.h>
#include <hal/board/hal_bridge.h>
#include <hal/hal.h>

namespace {
const char* TAG = "BillyPresence";

constexpr uint8_t kAdresse        = 0x23;  // LTR-553ALS-WA du CoreS3
constexpr uint32_t kPeriodeMs     = 200;
// Seuils au-dessus du fond. Avec 4 impulsions, le 7 novembre 2026 : fond 12 à 18, main à 5 cm vers
// 600, une personne penchée vers le robot (une quinzaine de centimètres) 30 à 60 seulement. D'où 15 impulsions
// (signal environ 3,7 fois plus fort) et des seuils bas, avec 3 lectures de suite contre les faux départs.
// Avec 15 impulsions : fond 61 à 63, bruit jamais plus de 7 au-dessus en 3 minutes ; +60 était trop haut
// (une seule pointe à +64 pendant l'essai), d'où +25 / +12.
constexpr int kSeuilEntree        = 25;    // au-dessus du fond : quelqu'un est là
constexpr int kSeuilSortie        = 12;    // en dessous : il est reparti
constexpr int kLecturesEntree     = 3;
constexpr uint32_t kEntreReactions = 60 * 1000;       // une réaction par minute au plus
constexpr uint32_t kEntreSaluts    = 60 * 60 * 1000;  // salut une fois par heure au plus
constexpr uint32_t kAirContentMs   = 5000;
// Pour tourner la tête vers ce qui bouge : l'objectif voit environ 60° de large et 45° de haut, donc un
// mouvement au bord de l'image (x = ±1) est à environ 30° du centre. Angles des servos en dixièmes de degré.
// Les signes sont à vérifier sur Billy (journal « mouvement : … centre (x, y) » contre ce qu'il fait).
constexpr int kDemiChampYaw        = 300;
constexpr int kDemiChampPitch      = 220;
constexpr int kSigneYaw            = 1;
constexpr int kSignePitch          = -1;  // y vers le bas dans l'image, pitch positif vers le haut
// Ce qui bouge, c'est surtout le corps : on vise un peu au-dessus (le visage), et la tête reste entre
// 8° et 30° (essai du 7 novembre 2026 : sinon Billy regardait les jambes).
constexpr float kViseeAuDessus     = 0.4f;
constexpr int kPitchMinReaction    = 80;
constexpr int kPitchMaxReaction    = 300;
// Le salut est un fichier Ogg Opus servi par billy-notif (GET /salut.ogg). Son adresse n'est pas
// compilée : la clé NVS billy:salut_url (par exemple http://192.0.2.10:5004/salut.ogg) passe en
// premier ; sinon on prend la machine de l'adresse OTA (clé wifi:ota_url ou CONFIG_OTA_URL), port 5004.
// Sans adresse utilisable, le robot ne salue pas.
std::string UrlSalut()
{
    Settings billy_nvs("billy", false);
    std::string url = billy_nvs.GetString("salut_url");
    if (!url.empty()) {
        return url;
    }
    Settings wifi("wifi", false);
    std::string ota = wifi.GetString("ota_url", CONFIG_OTA_URL);
    const auto debut = ota.find("://");
    if (debut == std::string::npos) {
        return "";
    }
    const auto hote_debut = debut + 3;
    const auto hote_fin   = ota.find_first_of(":/", hote_debut);
    const std::string hote = ota.substr(hote_debut, hote_fin - hote_debut);
    if (hote.empty() || hote.find("tenclass") != std::string::npos) {
        return "";  // adresse d'usine : pas de billy-notif là-bas
    }
    return ota.substr(0, hote_debut) + hote + ":5004/salut.ogg";
}
}  // namespace

/* ---------------------------------- Capteur LTR-553 ---------------------------------- */

bool billy::Proximite::ecrire(uint8_t reg, uint8_t val)
{
    const uint8_t buf[2] = {reg, val};
    return i2c_master_transmit(_dev, buf, 2, 50) == ESP_OK;
}

bool billy::Proximite::begin()
{
    i2c_master_bus_handle_t bus = hal_bridge::board_get_i2c_bus();
    if (!bus) {
        return false;
    }
    i2c_device_config_t cfg = {};
    cfg.dev_addr_length     = I2C_ADDR_BIT_LEN_7;
    cfg.device_address      = kAdresse;
    cfg.scl_speed_hz        = 400000;
    if (i2c_master_bus_add_device(bus, &cfg, &_dev) != ESP_OK) {
        _dev = nullptr;
        return false;
    }
    uint8_t reg = 0x86, id = 0;  // PART_ID : 0x92 attendu
    if (i2c_master_transmit_receive(_dev, &reg, 1, &id, 1, 50) != ESP_OK || (id & 0xF0) != 0x90) {
        ESP_LOGW(TAG, "LTR-553 absent (PART_ID 0x%02x)", id);
        i2c_master_bus_rm_device(_dev);
        _dev = nullptr;
        return false;
    }
    // LED IR 60 kHz, 100 %, 100 mA ; 15 impulsions (le maximum) ; mesure toutes les 100 ms ; proximité active
    return ecrire(0x82, 0x7F) && ecrire(0x83, 0x0F) && ecrire(0x84, 0x02) && ecrire(0x81, 0x03);
}

int billy::Proximite::lire()
{
    if (!_dev) {
        return -1;
    }
    uint8_t reg = 0x8D, d[2] = {0, 0};  // PS_DATA_0, PS_DATA_1
    if (i2c_master_transmit_receive(_dev, &reg, 1, d, 2, 50) != ESP_OK) {
        return -1;
    }
    return ((d[1] & 0x07) << 8) | d[0];
}

/* ------------------------------------ Réaction ------------------------------------ */

using namespace stackchan;

PresenceModifier::PresenceModifier()
{
    _actif = _capteur.begin();
    ESP_LOGI(TAG, "Il te regarde : capteur de proximité %s", _actif ? "prêt" : "indisponible");
    billy::Regard::demarrer();
}

bool PresenceModifier::nuit()
{
    // L'horloge de Billy est à l'heure locale (le serveur ajoute son décalage horaire)
    time_t t = time(nullptr);
    struct tm tm;
    localtime_r(&t, &tm);
    if (tm.tm_year + 1900 < 2025) {
        return true;  // heure inconnue : on reste discret
    }
    return tm.tm_hour >= 23 || tm.tm_hour < 7;
}

bool PresenceModifier::peut_reagir(Modifiable& stackchan, uint32_t now) const
{
    if (Application::GetInstance().GetDeviceState() != kDeviceStateIdle || nuit()) {
        return false;
    }
    return !_reaction_ms || now - _reaction_ms >= kEntreReactions;
}

void PresenceModifier::_update(Modifiable& stackchan)
{
    if (!stackchan.hasAvatar()) {
        return;
    }
    const uint32_t now = GetHAL().millis();

    // Caméra : quelqu'un qui bouge devant Billy
    float fx, fy;
    if (billy::Regard::mouvement(fx, fy)) {
        ESP_LOGI(TAG, "Quelqu'un bouge devant la caméra (%.2f, %.2f)", fx, fy);
        reagir(stackchan, now, fx, fy);
    }

    // Fin de l'air content : on revient au neutre si rien d'autre n'a changé l'expression
    if (_retour_ms && now >= _retour_ms) {
        _retour_ms = 0;
        auto& avatar = stackchan.avatar();
        if (avatar.getEmotion() == avatar::Emotion::Happy &&
            Application::GetInstance().GetDeviceState() == kDeviceStateIdle) {
            avatar.setEmotion(avatar::Emotion::Neutral);
            if (auto* billy = dynamic_cast<avatar::BillyAvatar*>(&avatar)) {
                billy->setExpressionName("neutral");
            }
        }
    }

    if (now - _lecture_ms < kPeriodeMs) {
        return;
    }
    _lecture_ms = now;
    // La caméra ne cherche que si Billy pourrait réagir, la tête immobile (image nette)
    billy::Regard::autoriser(peut_reagir(stackchan, now) && !stackchan.motion().isMoving());
    if (!_actif) {
        return;
    }
    const int v = _capteur.lire();
    if (v < 0) {
        if (++_erreurs >= 20) {
            ESP_LOGW(TAG, "Capteur de proximité muet : désactivé");
            _actif = false;
        }
        return;
    }
    _erreurs = 0;
    if (_base < 0) {
        _base = v;
    }
    // Pour régler les seuils : la plus forte valeur de chaque période de 5 s
    _max_journal = std::max(_max_journal, v);
    if (now - _journal_ms > 5000) {
        _journal_ms = now;
        ESP_LOGI(TAG, "proximité max %d sur 5 s (fond %.0f)%s", _max_journal, _base,
                 _proche ? ", quelqu'un est là" : "");
        _max_journal = 0;
    }

    if (!_proche) {
        if (v > _base + kSeuilEntree) {
            if (++_au_dessus >= kLecturesEntree) {
                _proche     = true;
                _au_dessus  = 0;
                _en_dessous = 0;
                ESP_LOGI(TAG, "Quelqu'un s'approche (%d, fond %.0f)", v, _base);
                reagir(stackchan, now);
            }
        } else {
            _au_dessus = 0;
            _base      = _base * 0.98f + v * 0.02f;  // fond qui suit lentement la pièce
        }
    } else if (v < _base + kSeuilSortie) {
        if (++_en_dessous >= 5) {
            _proche = false;
            ESP_LOGI(TAG, "Plus personne (%d)", v);
        }
    } else {
        _en_dessous = 0;
    }
}

void PresenceModifier::reagir(Modifiable& stackchan, uint32_t now, float x, float y)
{
    auto& app = Application::GetInstance();
    if (!peut_reagir(stackchan, now)) {
        return;
    }
    _reaction_ms = now;

    // Il te regarde : la tête vers ce qui bouge (ou droit devant, un peu levée, pour la main), et air
    // content quelques secondes
    auto& motion = stackchan.motion();
    int yaw = 0, pitch = 100;
    if (x != 0 || y != 0) {
        yaw   = std::clamp(motion.getCurrentYawAngle() + kSigneYaw * static_cast<int>(x * kDemiChampYaw), -800, 800);
        pitch = std::clamp(motion.getCurrentPitchAngle() +
                               kSignePitch * static_cast<int>((y - kViseeAuDessus) * kDemiChampPitch),
                           kPitchMinReaction, kPitchMaxReaction);
        ESP_LOGI(TAG, "Tête de (%d, %d) vers (%d, %d)", motion.getCurrentYawAngle(), motion.getCurrentPitchAngle(),
                 yaw, pitch);
    }
    motion.yawServo().moveWithSpeed(yaw, 300);
    motion.pitchServo().moveWithSpeed(pitch, 300);
    auto& avatar = stackchan.avatar();
    avatar.setEmotion(avatar::Emotion::Happy);
    if (auto* billy = dynamic_cast<avatar::BillyAvatar*>(&avatar)) {
        billy->setExpressionName("happy");
    }
    _retour_ms = now + kAirContentMs;

    if (!_deja_salue || now - _salut_ms >= kEntreSaluts) {
        _deja_salue = true;
        _salut_ms   = now;
        const std::string url = UrlSalut();
        if (!url.empty()) {
            ESP_LOGI(TAG, "Salut (%s)", url.c_str());
            app.PlayNotify(url, "Salut !");
        }
    }
}
