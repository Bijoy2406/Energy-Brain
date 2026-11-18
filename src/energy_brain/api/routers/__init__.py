"""One router per resource (#21). Later issues add their own module here and include it in ``app.py``:
#22 cloud event, #23 Pareto frontier, #25 operator override, #26 PDF report."""

from energy_brain.api.routers import backtest, days, explain, health, plan

ROUTERS = [health.router, days.router, plan.router, backtest.router, explain.router]

__all__ = ["ROUTERS"]
