/*
 * Outil MCP self.robot.play_moves : Billy joue lui-même une suite de pas (tête et LED) avec
 * sa propre horloge, pour qu'une danse envoyée en un seul message ne saccade pas à cause du
 * réseau. Les pas sont gardés en PSRAM et joués dans la boucle d'animation de StackChan.
 */
#pragma once
#include <string>

namespace billy {

// Analyse « moves » (tableau JSON de pas) et lance la suite, ou la complète si « append ».
// Un tableau vide arrête la suite en cours. Renvoie le compte rendu de l'outil MCP
// (« {"ok": true, ...} » ou « {"ok": false, "error": ...} »). À appeler sous le verrou LVGL.
std::string play_moves(const std::string& moves, bool append);

}  // namespace billy
