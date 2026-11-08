/*
 * « Il te regarde » : quand Billy, au repos, voit quelqu'un bouger (caméra, voir regard.h) ou qu'on passe
 * la main tout près du capteur de proximité, il tourne la tête vers la personne, prend l'air
 * content et, au plus une fois par heure, dit bonjour (fichier servi par billy-notif, adresse lue dans la NVS). Rien la nuit (23 h – 7 h).
 * Capteur de proximité LTR-553 du CoreS3 (I2C 0x23), lu toutes les 200 ms dans la boucle
 * d'animation de StackChan ; la caméra a sa propre tâche, de priorité basse.
 */
#pragma once
#include "../modifiable.h"
#include <driver/i2c_master.h>
#include <cstdint>

namespace billy {

class Proximite {
public:
    bool begin();  // false si le capteur ne répond pas
    int lire();    // 0..2047, ou -1 en cas d'erreur

private:
    bool ecrire(uint8_t reg, uint8_t val);
    i2c_master_dev_handle_t _dev = nullptr;
};

}  // namespace billy

namespace stackchan {

class PresenceModifier : public Modifier {
public:
    PresenceModifier();
    void _update(Modifiable& stackchan) override;

private:
    bool peut_reagir(Modifiable& stackchan, uint32_t now) const;
    // x, y : position du mouvement dans l'image (-1 à 1), ou 0, 0 pour regarder droit devant
    void reagir(Modifiable& stackchan, uint32_t now, float x = 0, float y = 0);
    static bool nuit();

    billy::Proximite _capteur;
    bool _actif           = false;
    int _erreurs          = 0;
    float _base           = -1;
    bool _proche          = false;
    int _au_dessus        = 0;
    int _en_dessous       = 0;
    uint32_t _lecture_ms  = 0;
    uint32_t _journal_ms  = 0;
    int _max_journal      = 0;
    uint32_t _reaction_ms = 0;
    uint32_t _salut_ms    = 0;
    bool _deja_salue      = false;
    uint32_t _retour_ms   = 0;  // fin de l'air content (0 : rien en cours)
};

}  // namespace stackchan
