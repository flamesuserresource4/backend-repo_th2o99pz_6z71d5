import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from database import db, create_document, get_documents
from schemas import Shipment, ShipmentCreate, ShipmentUpdate, LoginRequest, LoginResponse

# App setup
app = FastAPI(title="CargoConnect API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth/JWT setup
SECRET_KEY = os.getenv("JWT_SECRET", "dev-secret-change")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 12

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@cargoconnect.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@123")
ADMIN_HASH = pwd_context.hash(ADMIN_PASSWORD)

class TokenData(BaseModel):
    email: Optional[str] = None


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_admin(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(status_code=401, detail="Could not validate credentials")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None or email != ADMIN_EMAIL:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    return {"email": email}


@app.get("/")
def read_root():
    return {"message": "CargoConnect API running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "❌ Not Set",
        "database_name": "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            try:
                cols = db.list_collection_names()
                response["collections"] = cols
                response["database"] = "✅ Connected & Working"
                response["connection_status"] = "Connected"
                response["database_url"] = "✅ Set"
                response["database_name"] = getattr(db, 'name', '✅ Set')
            except Exception as e:
                response["database"] = f"⚠️ Connected but error: {str(e)[:80]}"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response


# Auth endpoints
@app.post("/auth/login", response_model=LoginResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    if form_data.username != ADMIN_EMAIL or not verify_password(form_data.password, ADMIN_HASH):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    access_token = create_access_token({"sub": ADMIN_EMAIL})
    return {"access_token": access_token, "token_type": "bearer"}


# Shipments
from bson import ObjectId
import qrcode
import io
from fastapi.responses import StreamingResponse


def generate_tracking_code() -> str:
    # Simple readable code CC-YYYYMMDD-XXXX
    now = datetime.now(timezone.utc)
    suffix = str(int(now.timestamp()))[-4:]
    return f"CC-{now.strftime('%Y%m%d')}-{suffix}"


@app.post("/shipments", response_model=dict)
async def create_shipment(payload: ShipmentCreate, admin=Depends(get_current_admin)):
    tracking_code = generate_tracking_code()
    doc = Shipment(
        tracking_code=tracking_code,
        status="Order Received",
        origin=payload.origin,
        destination=payload.destination,
        sender_name=payload.sender_name,
        receiver_name=payload.receiver_name,
        receiver_email=payload.receiver_email,
        receiver_phone=payload.receiver_phone,
        address=payload.address,
        country=payload.country,
        weight=payload.weight,
        description=payload.description,
        amount=payload.amount,
        timeline=[{"status": "Order Received", "timestamp": datetime.now(timezone.utc).isoformat()}],
        last_update=datetime.now(timezone.utc),
    ).model_dump()

    inserted_id = db["shipment"].insert_one(doc).inserted_id
    return {"id": str(inserted_id), "tracking_code": tracking_code}


@app.get("/shipments", response_model=list)
async def list_shipments(admin=Depends(get_current_admin)):
    items = list(db["shipment"].find().sort("last_update", -1).limit(200))
    for it in items:
        it["_id"] = str(it["_id"])
    return items


@app.get("/track/{tracking_code}", response_model=dict)
async def public_track(tracking_code: str):
    doc = db["shipment"].find_one({"tracking_code": tracking_code})
    if not doc:
        raise HTTPException(status_code=404, detail="Tracking Number Not Found")
    doc["_id"] = str(doc["_id"])
    return doc


@app.patch("/shipments/{tracking_code}", response_model=dict)
async def update_shipment(tracking_code: str, payload: ShipmentUpdate, admin=Depends(get_current_admin)):
    doc = db["shipment"].find_one({"tracking_code": tracking_code})
    if not doc:
        raise HTTPException(status_code=404, detail="Shipment not found")

    updates = {}
    if payload.status:
        updates["status"] = payload.status
        timeline = doc.get("timeline", [])
        timeline.append({"status": payload.status, "timestamp": datetime.now(timezone.utc).isoformat()})
        updates["timeline"] = timeline
    if payload.location is not None:
        updates["location"] = payload.location.model_dump()

    updates["last_update"] = datetime.now(timezone.utc)

    db["shipment"].update_one({"_id": doc["_id"]}, {"$set": updates})
    new_doc = db["shipment"].find_one({"_id": doc["_id"]})
    new_doc["_id"] = str(new_doc["_id"])
    return new_doc


@app.post("/shipments/{tracking_code}/proof")
async def upload_proof(tracking_code: str, file: UploadFile = File(...), admin=Depends(get_current_admin)):
    # In a real app, store in S3/Cloud. Here we store to local /logs and return pseudo URL
    content = await file.read()
    os.makedirs("logs", exist_ok=True)
    fname = f"logs/{tracking_code}-{file.filename}"
    with open(fname, "wb") as f:
        f.write(content)
    db["shipment"].update_one({"tracking_code": tracking_code}, {"$set": {"proof_of_delivery_url": f"/{fname}"}})
    return {"url": f"/{fname}"}


@app.get("/shipments/{tracking_code}/receipt.pdf")
async def generate_receipt(tracking_code: str):
    doc = db["shipment"].find_one({"tracking_code": tracking_code})
    if not doc:
        raise HTTPException(status_code=404, detail="Shipment not found")

    # Generate a QR code linking to tracking page
    track_url = f"/track/{tracking_code}"
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(track_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    # Make a simple PDF using reportlab-like approach via fpdf
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=16)
    pdf.set_text_color(230, 0, 0)  # CargoConnect red
    pdf.cell(200, 10, txt="CargoConnect Shipment Receipt", ln=True, align='C')

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt=f"Tracking: {tracking_code}", ln=True)
    pdf.cell(200, 10, txt=f"Sender: {doc.get('sender_name', '')}", ln=True)
    pdf.cell(200, 10, txt=f"Receiver: {doc.get('receiver_name', '')}", ln=True)
    pdf.cell(200, 10, txt=f"Amount: ${doc.get('amount', 0)}", ln=True)

    # Save QR temp file
    qr_path = f"logs/{tracking_code}-qr.png"
    os.makedirs("logs", exist_ok=True)
    with open(qr_path, 'wb') as qf:
        qf.write(buf.read())
    pdf.image(qr_path, x=160, y=20, w=30, h=30)

    out = io.BytesIO()
    pdf.output(out)
    out.seek(0)
    return StreamingResponse(out, media_type="application/pdf")


# Basic email notification via SMTP using environment-configured account
import smtplib
from email.message import EmailMessage


def send_email(to_email: str, subject: str, body: str):
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")
    if not (host and user and password):
        return False

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)
    return True


@app.post("/shipments/{tracking_code}/notify")
async def notify_receiver(tracking_code: str, admin=Depends(get_current_admin)):
    doc = db["shipment"].find_one({"tracking_code": tracking_code})
    if not doc:
        raise HTTPException(status_code=404, detail="Shipment not found")
    subject = f"CargoConnect Update: {tracking_code} - {doc.get('status')}"
    body = f"Hello {doc.get('receiver_name')},\n\nYour shipment {tracking_code} status is now: {doc.get('status')}.\nTrack here: {tracking_code}\n\nCargoConnect"
    ok = send_email(doc.get('receiver_email'), subject, body)
    return {"sent": ok}

