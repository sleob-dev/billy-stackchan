/*
 * « Il te regarde », par la caméra : une tâche de fond prend une image de la caméra (GC0308, 320×240)
 * toutes les 0,3 s, la réduit à 40×30 cases en luminosité et la compare à la précédente. Si une zone
 * assez grande change (quelqu'un qui arrive ou bouge devant Billy), elle donne le centre de cette zone.
 * C'est une détection de mouvement et non de visage : la détection de visage d'Espressif (esp-dl) prend
 * 1,6 Mo de flash et 21 Ko de mémoire interne, que Billy n'a pas (essai du 7 novembre 2026).
 * Elle ne travaille que lorsque la boucle d'animation l'y autorise : Billy au repos, de jour, la tête
 * immobile, et pas juste après une réaction. Elle tourne en priorité 1, sous l'affichage (3) et le son
 * (4 à 8) : elle ne prend que le temps processeur laissé libre.
 *
 * La réaction elle-même (tête, visage, le salut) reste dans PresenceModifier, appelée depuis la
 * boucle d'animation : la tâche ne touche ni aux servos ni à l'écran.
 */
#pragma once
#include <cstdint>

namespace billy {

class Regard {
public:
    static void demarrer();  // crée la tâche (une seule fois)
    // La boucle d'animation dit si la caméra peut chercher du mouvement en ce moment
    static void autoriser(bool oui);
    // Du mouvement vu depuis la dernière lecture ? x et y vont de -1 à 1 (centre de l'image en 0, y vers
    // le bas). Ne rend vrai qu'une fois par détection.
    static bool mouvement(float& x, float& y);
};

}  // namespace billy
