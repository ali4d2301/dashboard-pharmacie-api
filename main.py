from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI(title="Pharmacie API")

# Autoriser Vue (dev)
origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
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