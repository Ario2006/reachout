from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


EngineDep = Annotated[Engine, Depends(get_engine)]
