"""Install the dashboard without replacing the existing image-analysis workflow."""
from flask import render_template

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

    def unfinished(tab, label, **kwargs):
        return render_template("dashboard_ui/unfinished.html", tab_label=label, active=tab)

    routes = [
        ("/sites", "sites", "sites", "Sites"),
        ("/sites/<int:site_id>", "site_detail", "sites", "Sites"),
        ("/results", "results", "results", "Results"),
        ("/compare", "compare", "compare", "Compare Over Time"),
    ]
    for path, endpoint, tab, label in routes:
        def placeholder(tab=tab, label=label, **kwargs):
            return unfinished(tab, label, **kwargs)
        app.add_url_rule(path, endpoint, placeholder)
