from sqlalchemy import Column, String, Integer, Float, Boolean, ForeignKey, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from sqlalchemy import Sequence

Base = declarative_base()

class AnalysisSession(Base):
    __tablename__ = 'analysis_sessions'
    
    id = Column(Integer, Sequence('analysis_session_id_seq'), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    thread_dumps = relationship("ThreadDump", back_populates="session")

class ThreadDump(Base):
    __tablename__ = 'thread_dumps'
    
    id = Column(Integer, Sequence('thread_dump_id_seq'), primary_key=True)
    file_name = Column(String(255))
    timestamp = Column(DateTime)
    session_id = Column(Integer, ForeignKey('analysis_sessions.id'))
    total_threads = Column(Integer)
    running_threads = Column(Integer)
    blocked_threads = Column(Integer)
    waiting_threads = Column(Integer)
    timed_waiting_threads = Column(Integer)
    high_cpu_threads_exist = Column(Boolean, default=False)
    deadlocks_exist = Column(Boolean, default=False)
    lock_contentions_exist = Column(Boolean, default=False)
    waiting_on_object_monitor = Column(Integer, default=0)
    timed_waiting_on_object_monitor = Column(Integer, default=0)
    timed_waiting_sleeping = Column(Integer, default=0)
    waiting_parking = Column(Integer, default=0)
    timed_waiting_parking = Column(Integer, default=0)
    
    session = relationship("AnalysisSession", back_populates="thread_dumps")
    threads = relationship("ThreadDetails", back_populates="thread_dump")

class ThreadDetails(Base):
    __tablename__ = 'thread_details'
    
    id = Column(Integer, Sequence('thread_details_id_seq'), primary_key=True)
    thread_dump_id = Column(Integer, ForeignKey('thread_dumps.id'))
    session_id = Column(Integer, ForeignKey('analysis_sessions.id'))  # <-- Add this line
    name = Column(String(255))
    thread_id = Column(String(50))
    daemon = Column(Boolean)
    priority = Column(Integer)
    os_priority = Column(Integer)
    cpu_time = Column(Float)
    elapsed_time = Column(Float)
    tid = Column(String(50))
    nid = Column(String(50))
    state = Column(String(50))
    sub_state = Column(String(50))
    stack_trace = Column(String)
    
    thread_dump = relationship("ThreadDump", back_populates="threads")
    locks = relationship("ThreadLock", back_populates="thread")

class ThreadLock(Base):
    __tablename__ = 'thread_locks'

    id = Column(Integer, Sequence('thread_lock_id_seq'), primary_key=True)
    thread_id = Column(Integer, ForeignKey('thread_details.id'))
    lock_name = Column(String(255))
    lock_type = Column(String(50))  # MONITOR or SYNCHRONIZER
    is_owner = Column(Boolean, default=False)
    
    thread = relationship("ThreadDetails", back_populates="locks")