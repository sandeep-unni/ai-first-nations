"""Install the dashboard without replacing the existing image-analysis workflow."""
from collections import Counter
from datetime import date
import hashlib
import os

from flask import abort, render_template, request

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

    def class_colours(label):
        """Colour a model class from its label: a CSS colour name (orange) is used
        as-is; any other label gets a stable generated colour. Returns the generated
        colour and, when the label may be a colour name, the named variant."""
        hue = int(hashlib.sha256(str(label).encode()).hexdigest()[:6], 16) % 360
        named = f"color-mix(in srgb, {str(label).lower()} 88%, #333)" if str(label).isalpha() else None
        return f"hsl({hue} 45% 50%)", named

    def class_style(label):
        generated, named = class_colours(label)
        # Browsers ignore the second declaration when the label isn't a colour.
        return f"background: {generated};" + (f" background: {named};" if named else "")

    def donut_style(rows):
        """conic-gradient for rows with label and percent, with the same fallback rule."""
        def gradient(pick):
            stops, start = [], 0.0
            for row in rows:
                end = start + row["percent"]
                stops.append(f"{pick(class_colours(row['label']))} {start:.2f}% {end:.2f}%")
                start = end
            return f"background: conic-gradient({', '.join(stops)});"
        style = gradient(lambda colours: colours[0])
        if all(class_colours(row["label"])[1] for row in rows):
            style += " " + gradient(lambda colours: colours[1])
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

    def summarise(analyses):
        """Aggregate per-image analyses; every task, class and label comes from the data."""
        from survey_processing import tile_composition
        tasks, tiles, images, failed, models = {}, Counter(), set(), set(), set()
        for result in analyses:
            task = tasks.setdefault(result["analysis_type"], dict(classes=Counter(), statuses=Counter(), confidences=[]))
            task["statuses"][result["status"]] += 1
            images.add(result["image_id"])
            if result["status"] == "failed":
                failed.add(result["image_id"])
            if result.get("predicted_class"):
                task["classes"][result["predicted_class"]] += 1
                if result.get("confidence") is not None:
                    task["confidences"].append(float(result["confidence"]))
            tiles.update({label: int(n) for label, n in (result.get("tile_counts") or {}).items()})
            if result.get("model_name"):
                models.add((result["model_name"], result.get("model_version")))
        for task in tasks.values():
            total = sum(task["classes"].values())
            task["rows"] = [dict(label=label, count=count, percent=100 * count / total)
                            for label, count in task["classes"].most_common()]
            task["mean_confidence"] = (sum(task["confidences"]) / len(task["confidences"])
                                       if task["confidences"] else None)
        return dict(tasks=tasks, composition=tile_composition(tiles), images=len(images),
                    analysed=len(images - failed), failed=len(failed), models=sorted(models))

    @app.route("/results")
    def results():
        site_id = request.args.get("site_id", type=int)
        surveys, analyses = store.analysis_results(site_id)
        by_survey = {}
        for result in analyses:
            by_survey.setdefault(result["survey_id"], []).append(result)
        rows = [dict(survey, summary=summarise(by_survey.get(survey["survey_id"], []))) for survey in surveys]
        return render_template(
            "dashboard_ui/results.html",
            overall=summarise(analyses),
            surveys=rows,
            surveys_with_results=sum(1 for row in rows if row["summary"]["images"]),
            total_images=sum(row["image_count"] for row in rows),
            sites=store.list_sites(),
            site_id=site_id,
            class_style=class_style,
            donut_style=donut_style,
            status_label=status_label,
            active="results",
            demo_mode=store.demo_mode,
        )

    def unfinished(tab, label, **kwargs):
        return render_template("dashboard_ui/unfinished.html", tab_label=label, active=tab)

    routes = [
        ("/compare", "compare", "compare", "Compare Over Time"),
    ]
    for path, endpoint, tab, label in routes:
        def placeholder(tab=tab, label=label, **kwargs):
            return unfinished(tab, label, **kwargs)
        app.add_url_rule(path, endpoint, placeholder)
