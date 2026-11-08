/*
 * Skin « Billy » du firmware maison : le visage dessiné pour Billy
 * (A Visière, D Kawaii, E Visière kawaii, un thème tiré au sort pour chaque date).
 *
 * Les yeux et la bouche restent des Feature : les modifiers de StackChan (clignement,
 * parole, respiration, regard) les animent comme ceux de la skin par défaut. Le visage
 * entier est redessiné dans un canvas LVGL quand l'un d'eux change.
 */
#pragma once
#include "../../avatar/avatar.h"
#include "../../avatar/elements/feature.h"
#include "../default/default.h"
#include "billy_face.h"
#include <lvgl.h>
#include <smooth_lvgl.hpp>
#include <memory>

namespace stackchan::avatar {

class BillyAvatar;

class BillyFeature : public Feature {
public:
    explicit BillyFeature(BillyAvatar* owner) : _owner(owner)
    {
    }
    void setPosition(const uitk::Vector2i& position) override;
    void setWeight(int weight) override;
    void setEmotion(const Emotion& emotion) override;
    void setVisible(bool visible) override;
    uint32_t lastActiveMs() const
    {
        return _last_active_ms;
    }

private:
    BillyAvatar* _owner;
    uint32_t _last_active_ms = 0;
    friend class BillyAvatar;
};

class BillyAvatar : public Avatar {
public:
    ~BillyAvatar() override;

    void init(lv_obj_t* parent, const lv_font_t* font = &lv_font_montserrat_16);
    uitk::lvgl_cpp::Container* getPanel() const
    {
        return _pannel.get();
    }

    void update() override;
    void setEmotion(const Emotion& emotion) override;

    // Émotion envoyée par le serveur XiaoZhi (« happy », « laughing », « cool »…)
    void setExpressionName(const char* name);

    void markDirty()
    {
        _dirty = true;
    }

    // Style choisi à distance (outil MCP self.billy.set_style, par billy-commande) : « A », « D », « E »,
    // ou « auto » pour le thème du jour. Gardé en NVS (espace « billy », clé « style »). Faux si inconnu.
    static bool setStyle(const std::string& style);
    // {"style": style affiché, "choix": "auto" ou le style forcé}
    static std::string styleJson();

private:
    void redraw();
    billy::Look lookOfDay();

    std::unique_ptr<uitk::lvgl_cpp::Container> _pannel;
    lv_obj_t* _canvas = nullptr;
    billy::FaceRenderer _renderer;
    billy::FaceState _drawn;
    billy::Expr _expr       = billy::Expr::Neutre;
    billy::Look _look       = billy::Look::E;
    uint32_t _look_check_ms = 0;
    int _force_vu           = -2;  // dernier choix appliqué (voir setStyle)
    uint32_t _drop_start_ms = 0;
    uint32_t _blink_ms      = 0;
    uint32_t _reopen_ms     = 0;
    uint32_t _last_frame_ms = 0;
    int _stat_frames        = 0;
    int64_t _stat_us        = 0;
    int64_t _stat_px        = 0;
    int64_t _stat_max_us    = 0;  // pire image des 100 dernières
    int _stat_complets      = 0;  // images redessinées en entier (expression, style)
    int _last_eye_weight    = 100;
    bool _dirty             = true;
    bool _ready             = false;
};

}  // namespace stackchan::avatar
