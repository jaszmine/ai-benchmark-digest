from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from src.config import get_settings
from src.schemas import TrackOutput

settings = get_settings()


def render_digest_html(
    track_a: TrackOutput,
    track_b: TrackOutput,
    scorecard: dict,
    link_results: dict,
) -> str:
    # Resolves to src/templates where digest_email.html.j2 lives
    templates_dir = Path(__file__).resolve().parent.parent / "templates"
    
    # Fallback to project-level templates directory if needed
    if not templates_dir.exists():
        templates_dir = Path(__file__).resolve().parent.parent.parent / "templates"

    env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=True)
    template = env.get_template("digest_email.html.j2")

    return template.render(
        model_name=settings.model_name,
        track_a=track_a,
        track_b=track_b,
        scorecard=scorecard,
        links=link_results,
    )

# from pathlib import Path
# from jinja2 import Environment, FileSystemLoader
# from src.config import get_settings
# from src.schemas import TrackOutput

# settings = get_settings()


# def render_digest_html(
#     track_a: TrackOutput,
#     track_b: TrackOutput,
#     scorecard: dict,
#     link_results: dict,
# ) -> str:
#     templates_dir = Path(__file__).resolve().parent.parent.parent / "templates"
#     env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=True)
#     template = env.get_template("digest_email.html.j2")

#     return template.render(
#         model_name=settings.model_name,
#         track_a=track_a,
#         track_b=track_b,
#         scorecard=scorecard,
#         links=link_results,
#     )
