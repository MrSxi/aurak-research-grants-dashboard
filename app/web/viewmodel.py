"""Page view-models for the ASP.NET Core frontend.

The ASP.NET frontend renders every page with its own Razor views. It asks this backend for a page's data by
sending `X-Render-As: json` plus the list of templates it can render (`X-Render-Templates`). For those pages,
render() returns the template context as JSON instead of HTML. Requests without the header still get HTML.
"""
import types
from datetime import date, datetime
from decimal import Decimal

from fastapi.responses import JSONResponse
from markupsafe import Markup
from sqlalchemy import inspect as sa_inspect

from app import config, images
from app.services.matching import COMPONENTS

MEDIA_TYPE = "application/vnd.aurak.page+json"
HIDDEN_FIELDS = {"password_hash", "raw"}  # raw = the source API payload, never shown on a page
SECRET_WORDS = ("PASSWORD", "SECRET", "API_KEY", "TOKEN")


def wants_json(request, template: str) -> bool:
    if request.headers.get("x-render-as") != "json":
        return False
    return template in {t.strip() for t in request.headers.get("x-render-templates", "").split(",")}


def _config_dict(mod) -> dict:
    out = {}
    for k in dir(mod):
        if k.isupper() and not any(w in k for w in SECRET_WORDS):
            v = getattr(mod, k)
            if isinstance(v, (str, int, float, bool)) or v is None:
                out[k] = v
    return out


def jsonable(v):
    if getattr(v, "dtype", None) is not None and hasattr(v, "item"):  # numpy scalars (bool_, float32...)
        v = v.item()
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if v == v else None  # NaN -> null
    if isinstance(v, Markup):
        return str(v)
    if isinstance(v, datetime):
        return {"$dt": v.isoformat(), "s": str(v), "tz": v.tzname()}
    if isinstance(v, date):
        return {"$d": v.isoformat(), "s": str(v)}
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dict):
        return {("|".join(str(p) for p in k) if isinstance(k, tuple) else str(k)): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [jsonable(x) for x in v]
    if isinstance(v, types.ModuleType):
        return _config_dict(v)
    if isinstance(v, type):  # e.g. model classes in the database view
        return v.__name__
    try:
        mapper = sa_inspect(v).mapper  # ORM row
    except Exception:
        mapper = None
    if mapper is not None:
        return {a.key: jsonable(getattr(v, a.key)) for a in mapper.column_attrs if a.key not in HIDDEN_FIELDS}
    if hasattr(v, "_mapping"):  # SQLAlchemy Row
        return [jsonable(x) for x in v]
    return {"$str": str(v)}  # anything else (bytes, ...) as Python prints it


def page_response(template: str, ctx: dict) -> JSONResponse:
    model = {k: jsonable(v) for k, v in ctx.items() if k != "request"}
    model["_globals"] = {
        "COMPONENTS": {k: v[0] for k, v in COMPONENTS.items()},
        "config": _config_dict(config),
        "CLUSTER_IMAGES": images.CLUSTER_IMAGES,
        "PAGE_IMAGES": images.PAGE_IMAGES,
        "GRANT_POOL": images.GRANT_POOL,
        "DISALLOWED_IMAGE_PATHS": sorted(images.DISALLOWED_PATHS),
    }
    return JSONResponse({"template": template, "model": model}, media_type=MEDIA_TYPE)
