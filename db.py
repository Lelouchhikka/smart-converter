from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime

Base = declarative_base()
engine = create_engine('sqlite:///drones.db', echo=False)
SessionLocal = sessionmaker(bind=engine)

class Drone(Base):
    __tablename__ = 'drones'
    id = Column(String, primary_key=True, index=True)
    rtmp_url = Column(String, index=True, nullable=True)
    rtsp_url = Column(String, index=True, nullable=True)
    status = Column(String, default="inactive")
    source_type = Column(String)
    file_path = Column(String, nullable=True)
    loop_file = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    positions = relationship('Position', back_populates='drone', cascade="all, delete-orphan")

class Position(Base):
    __tablename__ = 'positions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    drone_id = Column(String, ForeignKey('drones.id'))
    timestamp = Column(DateTime, default=datetime.utcnow)
    lat = Column(Float)
    lon = Column(Float)
    altitude = Column(Float)
    speed = Column(Float)
    battery = Column(Float)
    signal_strength = Column(Float)
    drone = relationship('Drone', back_populates='positions')

# Создать таблицы при первом запуске
if __name__ == "__main__":
    Base.metadata.create_all(engine) 