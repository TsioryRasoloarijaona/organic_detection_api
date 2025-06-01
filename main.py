# api.py
from typing import List

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import sqlite3
import uvicorn
from datetime import datetime
import json
from fastapi import Query
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

app = FastAPI()

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def init_db():
    with sqlite3.connect('waste.db') as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detection_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            item_type TEXT CHECK(item_type IN ('organic')),
            confidence FLOAT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_time ON detections(detection_time)")

init_db()

class ConnectionManager:
    def __init__(self):
        self.active_connections = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            await connection.send_json(message)

manager = ConnectionManager()


@app.get("/")
async def ping():
    return "pong"

# API Endpoints
@app.post("/api/organic")
async def add_detection(confidence: float = Query(...)):
    
    # Debug print
    print(f"Received confidence: {confidence} (type: {type(confidence)})")
    with sqlite3.connect('waste.db') as conn:
        conn.execute(
            "INSERT INTO detections (item_type, confidence) VALUES (?, ?)",
            ('organic', confidence)
        )
        detection_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    
    stats = get_stats()
    await manager.broadcast({
        "event": "new_detection",
        "data": {
            "id": detection_id,
            "confidence": confidence,
            "timestamp": datetime.now().isoformat()
        },
        "stats": stats
    })
    return {"status": "success"}


@app.get("/api/stats")
def get_stats(hours: int = 24):
    with sqlite3.connect('waste.db') as conn:
        # Current day stats
        daily = conn.execute("""
            SELECT COUNT(*), AVG(confidence) 
            FROM detections 
            WHERE DATE(detection_time) = DATE('now')
        """).fetchone()
        
        # Hourly data for charts
        hourly = conn.execute("""
            SELECT strftime('%H', detection_time) as hour, 
                   COUNT(*) as count,
                   AVG(confidence) as avg_confidence
            FROM detections
            WHERE detection_time > datetime('now', '-24 hours')
            GROUP BY hour
        """).fetchall()
    
    return {
        "daily_total": daily[0],
        "daily_avg_confidence": daily[1],
        "compost_kg": daily[0] * 0.1,
        "hourly_data": [
            {"hour": h, "count": c, "confidence": float(conf or 0)} 
            for h, c, conf in hourly
        ]
    }


class MonthlyDetection(BaseModel):
    month_name: str
    detection_count: int
    masse: int


@app.get("/detections/monthly", response_model=List[MonthlyDetection])
async def get_monthly_detections():

    conn = sqlite3.connect('waste.db')
    cursor = conn.cursor()


    query = """
            WITH months AS (SELECT 1 AS month_num, 'Janvier' AS month_name \
                            UNION ALL \
                            SELECT 2, 'Février' \
                            UNION ALL \
                            SELECT 3, 'Mars' \
                            UNION ALL \
                            SELECT 4, 'Avril' \
                            UNION ALL \
                            SELECT 5, 'Mai' \
                            UNION ALL \
                            SELECT 6, 'Juin' \
                            UNION ALL \
                            SELECT 7, 'Juillet' \
                            UNION ALL \
                            SELECT 8, 'Août' \
                            UNION ALL \
                            SELECT 9, 'Septembre' \
                            UNION ALL \
                            SELECT 10, 'Octobre' \
                            UNION ALL \
                            SELECT 11, 'Novembre' \
                            UNION ALL \
                            SELECT 12, 'Décembre'),
                 detections_by_month AS (SELECT CAST(strftime('%m', detection_time) AS INTEGER) AS month_num, \
                                                COUNT(*) AS count
            FROM detections
            WHERE strftime('%Y', detection_time) = '2025'
            GROUP BY strftime('%m', detection_time)
                )
            SELECT m.month_name, \
                   COALESCE(db.count, 0)       AS detection_count, \
                   COALESCE(db.count, 0) * 100 AS masse
            FROM months m
                     LEFT JOIN detections_by_month db ON m.month_num = db.month_num
            ORDER BY m.month_num; \
            """

    cursor.execute(query)
    results = cursor.fetchall()
    conn.close()

    return [{
        "month_name": row[0],
        "detection_count": row[1],
        "masse": row[2]
    } for row in results]

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # Keep connection alive
    except WebSocketDisconnect:
        manager.active_connections.remove(websocket)
