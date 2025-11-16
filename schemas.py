"""
Database Schemas

CargoConnect data models for MongoDB using Pydantic.
Each Pydantic model represents a collection (lowercased class name).
"""

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Literal
from datetime import datetime

class Admin(BaseModel):
    name: str
    email: EmailStr
    hashed_password: str
    is_active: bool = True

class Location(BaseModel):
    lat: Optional[float] = Field(None, description="Latitude")
    lng: Optional[float] = Field(None, description="Longitude")
    city: Optional[str] = Field(None, description="City name if coords unknown")

StatusLiteral = Literal[
    "Order Received",
    "Package Pickup",
    "Sorting Center",
    "In Transit",
    "Customs Clearance",
    "Out for Delivery",
    "Delivered",
]

class Shipment(BaseModel):
    tracking_code: str = Field(..., description="Unique public tracking code")
    sender_name: str
    receiver_name: str
    receiver_email: EmailStr
    receiver_phone: Optional[str] = None
    address: str
    country: str
    weight: float = Field(..., ge=0)
    description: Optional[str] = None
    amount: float = Field(..., ge=0)
    origin: str
    destination: str
    status: StatusLiteral = "Order Received"
    timeline: List[dict] = Field(default_factory=list, description="List of status updates with timestamps")
    location: Optional[Location] = None
    last_update: Optional[datetime] = None
    proof_of_delivery_url: Optional[str] = None

class ShipmentCreate(BaseModel):
    sender_name: str
    receiver_name: str
    receiver_email: EmailStr
    receiver_phone: Optional[str] = None
    address: str
    country: str
    weight: float = Field(..., ge=0)
    description: Optional[str] = None
    amount: float = Field(..., ge=0)
    origin: str
    destination: str

class ShipmentUpdate(BaseModel):
    status: Optional[StatusLiteral] = None
    location: Optional[Location] = None

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
