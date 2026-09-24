"""Install the dashboard without replacing the existing image-analysis workflow."""
from datetime import date
import hashlib
import os

from flask import abort, render_template

from dashboard_store import get_store
from survey_flow import install_survey_flow


def install_dashboard(app, store=None):
    store = store if store is not None else get_store()
    install_survey_flow(app, store)

    @app.route("/dashboard")
    def dashboard():
        return render_template(
            "dashboard_ui/dashboard.html",
            summary=store.dashboard_summary(),
            recent_surveys=store.list_surveys(limit=5),
            active="dashboard",
            demo_mode=store.demo_mode,
        )

    # Sites settings come from the environment so deployments can tune them.
    app.config.setdefault("SITES_RECENT_DAYS", int(os.getenv("AIFN_SITES_RECENT_DAYS", "90")))
    app.config.setdefault("SITES_MAP_URL", os.getenv(
        "AIFN_SITES_MAP_URL", "https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=14/{lat}/{lon}"))

    def site_view(site, today):
        """Derived values shared by the Sites list and a single site page."""
        from survey_processing import tile_composition
        site["composition"] = tile_composition(site.get("latest_tiles"))
        last = site.get("last_survey_date")
        site["days_since"] = (today - date.fromisoformat(str(last))).days if last else None
        site["recent"] = site["days_since"] is not None and site["days_since"] <= app.config["SITES_RECENT_DAYS"]
        has_coords = site.get("latitude") is not None and site.get("longitude") is not None
        site["map_url"] = (app.config["SITES_MAP_URL"].format(lat=site["latitude"], lon=site["longitude"])
                           if has_coords and app.config["SITES_MAP_URL"] else None)
        return site

    def class_style(label):
        """Colour a model class from its label: a CSS colour name (orange) is used
        as-is; any other label gets a stable generated colour."""
        hue = int(hashlib.sha256(str(label).encode()).hexdigest()[:6], 16) % 360
        style = f"background: hsl({hue} 45% 50%);"
        if str(label).isalpha():
            # Browsers ignore this second declaration when the label isn't a colour.
            style += f" background: color-mix(in srgb, {str(label).lower()} 88%, #333);"
        return style

    def status_label(status):
        return str(status).replace("_", " ").capitalize() if status else ""

    @app.route("/sites")
    def sites():
        today = date.today()
        overview = [site_view(site, today) for site in store.site_overview()]
        return render_template(
            "dashboard_ui/sites.html",
            sites=overview,
            surveyed=sum(1 for site in overview if site["survey_count"]),
            states=sorted({site["state"] for site in overview if site.get("state")}),
            recent_days=app.config["SITES_RECENT_DAYS"],
            class_style=class_style,
            status_label=status_label,
            active="sites",
            demo_mode=store.demo_mode,
        )

    @app.route("/sites/<int:site_id>")
    def site_detail(site_id):
        rows = store.site_overview(site_id)
        if not rows:
            abort(404)
        return render_template(
            "dashboard_ui/site_detail.html",
            site=site_view(rows[0], date.today()),
            surveys=store.survey_history(site_id),
            class_style=class_style,
            status_label=status_label,
            active="sites",
            demo_mode=store.demo_mode,
        )

    def unfinished(tab, label, **kwargs):
        return render_template("dashboard_ui/unfinished.html", tab_label=label, active=tab)

    routes = [
        ("/results", "results", "results", "Results"),
        ("/compare", "compare", "compare", "Compare Over Time"),
    ]
    for path, endpoint, tab, label in routes:
        def placeholder(tab=tab, label=label, **kwargs):
            return unfinished(tab, label, **kwargs)
        app.add_url_rule(path, endpoint, placeholder)
