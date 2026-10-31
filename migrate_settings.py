import asyncio
from app.core.database import AsyncSessionLocal
from sqlalchemy import text

async def run_migration():
    async with AsyncSessionLocal() as session:
        # Check current columns
        res = await session.execute(text("PRAGMA table_info(risk_limits)"))
        cols = [c[1] for c in res.fetchall()]
        print("Existing risk_limits columns:", cols)

        new_cols = [
            ("trade_allocation_pct", "NUMERIC(5,2) DEFAULT 30.00 NOT NULL"),
            ("take_profit_pct", "NUMERIC(5,2) DEFAULT 10.00 NOT NULL"),
            ("stop_loss_pct", "NUMERIC(5,2) DEFAULT 5.00 NOT NULL"),
            ("trailing_stop_enabled", "BOOLEAN DEFAULT 1 NOT NULL"),
            ("trailing_stop_callback_pct", "NUMERIC(5,2) DEFAULT 0.50 NOT NULL"),
        ]

        for col_name, col_def in new_cols:
            if col_name not in cols:
                sql = f"ALTER TABLE risk_limits ADD COLUMN {col_name} {col_def}"
                print(f"Executing: {sql}")
                await session.execute(text(sql))
        
        await session.commit()

        res2 = await session.execute(text("PRAGMA table_info(risk_limits)"))
        print("Updated risk_limits columns:", [c[1] for c in res2.fetchall()])

if __name__ == "__main__":
    asyncio.run(run_migration())
