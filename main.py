import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from settings import settings

#app = FastAPI(title="Pharmacie API")

ENV = os.getenv("ENV", "dev")
app = FastAPI(
    title="Pharmacie API",
    docs_url=None if ENV == "prod" else "/docs",
    redoc_url=None if ENV == "prod" else "/redoc",
)

# Autoriser les origines (Render / dev / prod)
origins = [o.strip().rstrip("/") for o in settings.CORS_ORIGINS.split(",") if o.strip()]

# fallback dev si variable absente
if not origins:
    origins = ["http://localhost:5173", "http://127.0.0.1:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 🔌 on branche les routes
from routes.insert_prod import router as insert_prod
app.include_router(insert_prod)

from routes.edit_prod import router as products_edit
app.include_router(products_edit)

from routes.dashboard import router as dashboard_router
app.include_router(dashboard_router)

from routes.products import router as products_router
app.include_router(products_router)

from routes.hist_mouvements import router as hist_mouvements
app.include_router(hist_mouvements)

from routes.insert_move import router as insert_move
app.include_router(insert_move)

from routes.edit_movement import router as edit_move
app.include_router(edit_move)

from routes.auth import router as auth_router
app.include_router(auth_router)

from routes.reports import router as reports_router
app.include_router(reports_router)
