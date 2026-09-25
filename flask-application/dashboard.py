"""Install the dashboard without replacing the existing image-analysis workflow."""
from collections import Counter
from datetime import date
import hashlib
import os
import hmac
import secrets

from flask import (abort, flash, redirect, render_template, request, session, url_for)
from dashboard_store import get_store
from survey_flow import install_survey_flow


# Colour-named model classes drawn together, stepped so every pair stays distinct
# for colour-blind readers and each clears the chart lightness band on white
# (checked with the dataviz palette validator, all pairs). Labels beside every
# mark carry identity too, since yellow is below 3:1 contrast on its own.
VALIDATED_CLASS_COLOURS = {"orange": "#e2622a", "red": "#a61b1b", "yellow": "#d8ac0c"}


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
    # A mistyped value falls back to the default instead of stopping the app.
    try:
        recent_days = int(os.getenv("AIFN_SITES_RECENT_DAYS", "90"))
    except ValueError:
        app.logger.warning("AIFN_SITES_RECENT_DAYS must be a whole number; using 90.")
        recent_days = 90
    app.config.setdefault("SITES_RECENT_DAYS", recent_days)
    app.config.setdefault("SITES_MAP_URL", os.getenv(
        "AIFN_SITES_MAP_URL", "https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=14/{lat}/{lon}"))

    # Formats browsers can show inline; TIFF originals are listed without a preview.
    app.config.setdefault("RESULTS_PREVIEW_TYPES", tuple(
        kind.strip().lstrip(".") for kind in
        os.getenv("AIFN_RESULTS_PREVIEW_TYPES", "jpg,jpeg,png").lower().split(",") if kind.strip()))

    def site_view(site, today):
        """Derived values shared by the Sites list and a single site page."""
        from survey_processing import tile_composition
        site["composition"] = tile_composition(site.get("latest_tiles"))
        last = site.get("last_survey_date")
        site["days_since"] = (today - date.fromisoformat(str(last))).days if last else None
        site["recent"] = site["days_since"] is not None and site["days_since"] <= app.config["SITES_RECENT_DAYS"]
        has_coords = site.get("latitude") is not None and site.get("longitude") is not None
        site["map_url"] = None
        if has_coords and app.config["SITES_MAP_URL"]:
            try:
                site["map_url"] = app.config["SITES_MAP_URL"].format(lat=site["latitude"], lon=site["longitude"])
            except (KeyError, IndexError, ValueError):
                # A malformed template only hides the map link; the page still loads.
                app.logger.warning("AIFN_SITES_MAP_URL must use only {lat} and {lon} placeholders.")
        return site

    def class_colours(label):
        """Colour a model class from its label: a colour-named class (orange) is drawn
        in that colour, using a validated step where one exists; any other label gets
        a stable generated colour. Returns the generated colour and, when the label
        may be a colour name, the named variant."""
        hue = int(hashlib.sha256(str(label).encode()).hexdigest()[:6], 16) % 360
        name = str(label).lower()
        named = VALIDATED_CLASS_COLOURS.get(name) or (
            f"color-mix(in srgb, {name} 88%, #333)" if name.isalpha() else None)
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
        
        session.setdefault("site_csrf", secrets.token_urlsafe(32))
        
        return render_template(
            "dashboard_ui/site_detail.html",
            site=site_view(rows[0], date.today()),
            surveys=store.survey_history(site_id),
            csrf_token=session["site_csrf"],
            class_style=class_style,
            status_label=status_label,
            active="sites",
            demo_mode=store.demo_mode,
        )

    @app.post("/sites/<int:site_id>/delete")
    def delete_site(site_id):
        supplied = request.form.get("csrf_token", "")
        expected = session.get("site_csrf", "") or "invalid"

        if not hmac.compare_digest(supplied, expected):
            abort(400)
        if request.form.get("confirm") != "delete":
            abort(400)

        try:
            deleted = store.delete_site(site_id)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(
                url_for("site_detail", site_id=site_id),
                code=303,
            )

        if deleted is None:
            abort(404)

        flash(f"{deleted['site_name']} deleted.", "success")
        return redirect(url_for("sites"), code=303)

    def summarise(analyses):
        """Aggregate per-image analyses; every task, class and label comes from the data."""
        from survey_processing import tile_composition
        tasks, tiles, images, failed, models = {}, Counter(), set(), set(), set()
        analysed_at = None
        for result in analyses:
            task = tasks.setdefault(result["analysis_type"], dict(classes=Counter(), statuses=Counter(),
                                                                  confidences=[], models=set()))
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
                task["models"].add(result.get("model_version") or result["model_name"])
            if result.get("processed_at") and (analysed_at is None or result["processed_at"] > analysed_at):
                analysed_at = result["processed_at"]
        for task in tasks.values():
            total = sum(task["classes"].values())
            task["rows"] = [dict(label=label, count=count, percent=100 * count / total)
                            for label, count in task["classes"].most_common()]
            task["mean_confidence"] = (sum(task["confidences"]) / len(task["confidences"])
                                       if task["confidences"] else None)
            task["models"] = sorted(task["models"])
        return dict(tasks=tasks, composition=tile_composition(tiles), images=len(images),
                    analysed=len(images - failed), failed=len(failed), models=sorted(models),
                    analysed_at=analysed_at)

    def preview_url(image):
        """Browser-viewable original stored by this app, if there is one."""
        if (image and image.get("storage_provider") == "local"
                and image.get("storage_container_id") == app.config["SURVEY_STORAGE_ID"]
                and str(image.get("file_type", "")).lower() in app.config["RESULTS_PREVIEW_TYPES"]):
            return url_for("survey_image", filename=image["storage_item_id"])
        return None

    @app.route("/results")
    def results():
        site_id = request.args.get("site_id", type=int)
        surveys, analyses = store.analysis_results(site_id)
        by_survey = {}
        for result in analyses:
            by_survey.setdefault(result["survey_id"], []).append(result)
        rows = [dict(survey, summary=summarise(by_survey.get(survey["survey_id"], [])),
                     preview_url=preview_url(survey.get("preview"))) for survey in surveys]
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

    def compare_change(before, after):
        """Differences between two survey summaries; labels and tasks come from the data."""
        old, new = before["summary"], after["summary"]
        old_mix = {row["label"]: row["percent"] for row in old["composition"]["rows"]}
        new_mix = {row["label"]: row["percent"] for row in new["composition"]["rows"]}
        classes = [dict(label=label, before=old_mix.get(label, 0.0), after=new_mix.get(label, 0.0),
                        delta=new_mix.get(label, 0.0) - old_mix.get(label, 0.0))
                   for label in sorted(set(old_mix) | set(new_mix))]
        tasks = []
        for name in sorted(set(old["tasks"]) | set(new["tasks"])):
            a, b = old["tasks"].get(name), new["tasks"].get(name)
            top = lambda task: task["rows"][0]["label"] if task and task["rows"] else None
            conf = lambda task: task["mean_confidence"] if task else None
            models = lambda task: task["models"] if task else []
            tasks.append(dict(name=name, before=top(a), after=top(b), before_conf=conf(a), after_conf=conf(b),
                              before_models=models(a), after_models=models(b),
                              model_changed=models(a) != models(b)))
        both_tiles = old["composition"]["total_tiles"] and new["composition"]["total_tiles"]
        days = (date.fromisoformat(str(after["survey_date"])) - date.fromisoformat(str(before["survey_date"]))).days
        return dict(classes=classes, tasks=tasks, days=days,
                    mangrove_before=old["composition"]["mangrove_percent"] if both_tiles else None,
                    mangrove_after=new["composition"]["mangrove_percent"] if both_tiles else None,
                    models_changed=any(task["model_changed"] for task in tasks))

    def trend_chart(timeline, before, after, width=640, height=230):
        """SVG geometry for mangrove share across the site's surveys (single series)."""
        points = [s for s in timeline if s["summary"]["composition"]["total_tiles"]]
        if not points:
            return None
        left, right, top, bottom = 46, 24, 18, 42
        plot_w, plot_h = width - left - right, height - top - bottom
        days = [date.fromisoformat(str(s["survey_date"])).toordinal() for s in points]
        # Real time spacing, unless dates repeat (then even spacing keeps points apart).
        spread = max(days) - min(days)
        if len(points) == 1:
            xs = [left + plot_w / 2]
        elif spread and len(set(days)) == len(days):
            xs = [left + plot_w * (d - min(days)) / spread for d in days]
        else:
            xs = [left + plot_w * i / (len(points) - 1) for i in range(len(points))]
        marks = []
        for x, s in zip(xs, points):
            value = s["summary"]["composition"]["mangrove_percent"]
            role = ("From" if before and s["survey_id"] == before["survey_id"] else
                    "To" if after and s["survey_id"] == after["survey_id"] else "")
            marks.append(dict(x=x, y=top + plot_h * (1 - value / 100), value=value, role=role,
                              date=str(s["survey_date"]), name=s["survey_name"] or s["survey_code"],
                              survey_id=s["survey_id"]))
        line = " ".join(f"{'M' if i == 0 else 'L'}{m['x']:.1f},{m['y']:.1f}" for i, m in enumerate(marks))
        area = (f"{line} L{marks[-1]['x']:.1f},{top + plot_h:.1f} L{marks[0]['x']:.1f},{top + plot_h:.1f} Z"
                if len(marks) > 1 else None)
        # Date labels: all when few, otherwise first, last and the compared pair.
        labelled = {0, len(marks) - 1} | {i for i, m in enumerate(marks) if m["role"]}
        for i, m in enumerate(marks):
            m["show_date"] = len(marks) <= 6 or i in labelled
        ticks = [dict(value=v, y=top + plot_h * (1 - v / 100)) for v in (0, 25, 50, 75, 100)]
        return dict(width=width, height=height, left=left, right=width - right, top=top,
                    base=top + plot_h, marks=marks, line=line, area=area, ticks=ticks)

    @app.route("/compare")
    def compare():
        site_id = request.args.get("site_id", type=int)
        # A chosen site loads only its own surveys; with none chosen, every site is
        # read once to pick the one with the most analysed surveys.
        surveys, analyses = store.analysis_results(site_id)
        by_survey = {}
        for result in analyses:
            by_survey.setdefault(result["survey_id"], []).append(result)
        by_site = {}
        for survey in reversed(surveys):  # oldest first
            row = dict(survey, summary=summarise(by_survey.get(survey["survey_id"], [])))
            by_site.setdefault(survey["site_id"], []).append(row)
        analysed = {site: [s for s in rows if s["summary"]["images"]] for site, rows in by_site.items()}
        if site_id is None and analysed:
            # Open on the site with the most analysed surveys (latest activity breaks ties).
            site_id = max(analysed, key=lambda site: (len(analysed[site]), by_site[site][-1]["survey_id"]))
        timeline = by_site.get(site_id, [])
        choices = analysed.get(site_id, [])
        ids = [s["survey_id"] for s in choices]
        after_id = request.args.get("to", type=int)
        before_id = request.args.get("from", type=int)
        after_id = after_id if after_id in ids else (ids[-1] if ids else None)
        if before_id not in ids or before_id == after_id:
            earlier = [i for i in ids if ids.index(i) < ids.index(after_id)] if after_id else []
            before_id = earlier[-1] if earlier else next((i for i in ids if i != after_id), None)
        before = next((s for s in choices if s["survey_id"] == before_id), None)
        after = next((s for s in choices if s["survey_id"] == after_id), None)
        if before and after and (str(before["survey_date"]), before_id) > (str(after["survey_date"]), after_id):
            before, after = after, before  # always read change forwards in time
        return render_template(
            "dashboard_ui/compare.html",
            sites=store.list_sites(),
            site_id=site_id,
            timeline=timeline,
            choices=choices,
            before=before,
            after=after,
            change=compare_change(before, after) if before and after else None,
            trend=trend_chart(timeline, before, after),
            class_style=class_style,
            status_label=status_label,
            active="compare",
            demo_mode=store.demo_mode,
        )
