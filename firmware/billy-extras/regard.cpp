/*
 * « Il te regarde », par la caméra : voir regard.h.
 */
#include "regard.h"
#include <algorithm>
#include <atomic>
#include <cstdlib>
#include <cstring>
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <hal/board/hal_bridge.h>
#include <hal/board/stackchan_camera.h>

namespace {
const char* TAG = "BillyRegard";

// Une image toutes les 0,3 s au plus (avec les deux captures, environ 0,45 s par analyse). Les moments où
// la tête est immobile sont courts : à 0,5 s et avec une image jetée en plus, Billy n'analysait que 2 à
// 8 images par minute (essai du 7 novembre 2026).
constexpr uint32_t kPeriodeMs = 300;
// L'image (320×240) est réduite à 40×30 cases de 8×8 pixels, en luminosité (0 à 255).
constexpr int kCol = 40, kLig = 30, kCases = kCol * kLig;
// Une case « bouge » si sa luminosité change de plus de kSeuilCase, une fois corrigé le changement
// d'ensemble (exposition automatique de la caméra, lampe allumée).
constexpr int kSeuilCase = 20;
// Mouvement retenu : au moins 1,5 % des cases (une personne qui passe à 2 m en couvre bien plus), mais
// pas plus de la moitié (lumière qui change, caméra qui bouge).
constexpr int kCasesMin = 18;
constexpr int kCasesMax = kCases / 2;
// Contre les faux départs : un grand mouvement (5 % de l'image) suffit ; un plus petit doit être revu
// dans les deux images suivantes. Essai du 7 novembre 2026 : exiger deux images de suite ratait la personne,
// les images à mouvement étant souvent séparées par une image calme.
constexpr int kCasesFort = 60;
constexpr int kEcartMax  = 2;
constexpr int64_t kJournalUs = 60 * 1000 * 1000LL;

std::atomic<bool> s_autorise{false};
std::atomic<bool> s_nouveau{false};
std::atomic<int> s_x1000{0}, s_y1000{0};
TaskHandle_t s_tache = nullptr;

// Luminosité moyenne de chaque case (un pixel sur deux en largeur et en hauteur suffit)
bool reduire(const uint8_t* data, size_t taille, int w, int h, int format, uint8_t* sortie)
{
    if (w < kCol * 8 || h < kLig * 8) {
        return false;
    }
    const int bw = w / kCol, bh = h / kLig;
    for (int cy = 0; cy < kLig; ++cy) {
        for (int cx = 0; cx < kCol; ++cx) {
            uint32_t somme = 0, n = 0;
            for (int y = cy * bh; y < (cy + 1) * bh; y += 2) {
                for (int x = cx * bw; x < (cx + 1) * bw; x += 2) {
                    const size_t i = (static_cast<size_t>(y) * w + x) * 2;  // 2 octets par pixel
                    if (i + 1 >= taille) {
                        return false;
                    }
                    uint32_t luma;
                    switch (format) {
                        case V4L2_PIX_FMT_YUYV: luma = data[i]; break;      // Y U Y V
                        case V4L2_PIX_FMT_UYVY: luma = data[i + 1]; break;  // U Y V Y
                        case V4L2_PIX_FMT_RGB565: {
                            const uint16_t p = data[i] | (data[i + 1] << 8);
                            luma = (((p >> 11) & 0x1F) * 8 * 77 + ((p >> 5) & 0x3F) * 4 * 150 + (p & 0x1F) * 8 * 29) >> 8;
                            break;
                        }
                        default: return false;
                    }
                    somme += luma;
                    ++n;
                }
            }
            sortie[cy * kCol + cx] = static_cast<uint8_t>(somme / n);
        }
    }
    return true;
}

void tache(void*)
{
    // EspVideo écrit une ligne d'avertissement à chaque image capturée : on la fait taire.
    esp_log_level_set("EspVideo", ESP_LOG_ERROR);
    // Deux petites images de 1,2 Ko, en PSRAM : la mémoire interne est comptée
    auto* avant      = static_cast<uint8_t*>(heap_caps_malloc(kCases, MALLOC_CAP_SPIRAM));
    auto* maintenant = static_cast<uint8_t*>(heap_caps_malloc(kCases, MALLOC_CAP_SPIRAM));
    if (!avant || !maintenant) {
        ESP_LOGW(TAG, "pas de mémoire pour la caméra : arrêt");
        vTaskDelete(nullptr);
        return;
    }
    bool reference      = false;  // « avant » contient une image valable
    bool format_signale = false;
    bool premier        = true;
    int depuis          = 99;     // analyses depuis le dernier petit mouvement
    // Mesures, journalisées une fois par minute quand la caméra a travaillé
    int analyses = 0, mouvements = 0, cases_max = 0, tours = 0, tours_autorises = 0;
    int64_t total_us = 0, pire_us = 0;
    int64_t journal_us = esp_timer_get_time();

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(kPeriodeMs));
        ++tours;
        if (s_autorise.load()) {
            ++tours_autorises;
        }
        if (tours_autorises > 0 && esp_timer_get_time() - journal_us > kJournalUs) {
            ESP_LOGI(TAG,
                     "1 min : caméra permise %d %% du temps, %d images comparées (%lld ms en moyenne, %lld ms au pire), "
                     "%d avec du mouvement, %d cases au plus",
                     tours_autorises * 100 / tours, analyses, analyses ? total_us / analyses / 1000 : 0, pire_us / 1000,
                     mouvements, cases_max);
            analyses = mouvements = cases_max = tours = tours_autorises = 0;
            total_us = pire_us = 0;
            journal_us = esp_timer_get_time();
        }
        if (!s_autorise.load()) {
            // Tête qui bouge, Billy occupé : l'image de référence ne vaut plus rien
            reference = false;
            depuis    = 99;
            continue;
        }
        StackChanCamera* camera = hal_bridge::board_get_camera();
        if (!camera) {
            continue;
        }
        const int64_t t0 = esp_timer_get_time();
        // La caméra sert aussi aux photos et au flux vidéo : si elle est prise, on passe notre tour.
        auto verrou = camera->TryLockFrame();
        if (!verrou.owns_lock()) {
            continue;
        }
        // La caméra n'a qu'un tampon, rempli juste après la capture précédente : la première image peut
        // dater de la dernière analyse. On la jette et on garde la suivante.
        if (!camera->StreamCaptures() || !camera->StreamCaptures()) {
            continue;
        }
        const int format = camera->GetFrameFormat();
        const bool ok = reduire(camera->GetFrameData(), camera->GetFrameSize(), camera->GetFrameWidth(),
                                camera->GetFrameHeight(), format, maintenant);
        if (premier) {
            premier = false;
            ESP_LOGI(TAG, "caméra : images %dx%d, format 0x%08x%s", camera->GetFrameWidth(), camera->GetFrameHeight(),
                     format, ok ? "" : " (non géré)");
        }
        verrou.unlock();
        if (!ok) {
            if (!format_signale) {
                ESP_LOGW(TAG, "format d'image 0x%08x non géré", format);
                format_signale = true;
            }
            continue;
        }
        if (!reference) {
            memcpy(avant, maintenant, kCases);
            reference = true;
            continue;
        }

        // Changement d'ensemble (exposition, lumière de la pièce), retiré avant de comparer les cases
        int32_t somme_avant = 0, somme_maintenant = 0;
        for (int i = 0; i < kCases; ++i) {
            somme_avant += avant[i];
            somme_maintenant += maintenant[i];
        }
        const int decalage = (somme_maintenant - somme_avant) / kCases;
        int nb = 0, sx = 0, sy = 0;
        for (int i = 0; i < kCases; ++i) {
            if (std::abs(maintenant[i] - avant[i] - decalage) > kSeuilCase) {
                ++nb;
                sx += i % kCol;
                sy += i / kCol;
            }
        }
        memcpy(avant, maintenant, kCases);
        const int64_t dt = esp_timer_get_time() - t0;
        ++analyses;
        total_us += dt;
        pire_us   = std::max(pire_us, dt);
        cases_max = std::max(cases_max, nb);

        if (nb < kCasesMin || nb > kCasesMax) {
            depuis = std::min(depuis + 1, 99);
            continue;
        }
        ++mouvements;
        const float x = ((sx / static_cast<float>(nb)) + 0.5f - kCol / 2.0f) / (kCol / 2.0f);
        const float y = ((sy / static_cast<float>(nb)) + 0.5f - kLig / 2.0f) / (kLig / 2.0f);
        ESP_LOGI(TAG, "mouvement : %d cases sur %d, centre (%.2f, %.2f), lumière %+d", nb, kCases, x, y, decalage);
        if (nb >= kCasesFort || depuis <= kEcartMax) {
            depuis = 99;
            s_x1000.store(static_cast<int>(x * 1000));
            s_y1000.store(static_cast<int>(y * 1000));
            s_nouveau.store(true);
        } else {
            depuis = 1;
        }
    }
}
}  // namespace

void billy::Regard::demarrer()
{
    if (s_tache) {
        return;
    }
    // Pile en PSRAM (comme la tâche notify) : la mémoire interne est comptée. Cœur 1, priorité 1.
    if (xTaskCreatePinnedToCoreWithCaps(tache, "billy_regard", 6144, nullptr, 1, &s_tache, 1, MALLOC_CAP_SPIRAM) !=
        pdPASS) {
        s_tache = nullptr;
        ESP_LOGW(TAG, "tâche de la caméra non créée");
        return;
    }
    ESP_LOGI(TAG, "Il te regarde : détection de mouvement par la caméra prête");
}

void billy::Regard::autoriser(bool oui)
{
    s_autorise.store(oui);
    if (!oui) {
        s_nouveau.store(false);
    }
}

bool billy::Regard::mouvement(float& x, float& y)
{
    if (!s_nouveau.exchange(false)) {
        return false;
    }
    x = s_x1000.load() / 1000.0f;
    y = s_y1000.load() / 1000.0f;
    return true;
}
