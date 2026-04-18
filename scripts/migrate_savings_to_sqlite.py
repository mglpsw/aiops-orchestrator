#!/usr/bin/env python3
import os
import sqlite3
import json
import logging
from pathlib import Path

# Permite override via variável de ambiente para execução fora do container
DATA_DIR = Path(os.getenv("AIOPS_DATA_DIR", "/app/data"))
SAVINGS_JSON = DATA_DIR / "savings.json"
DB_PATH = DATA_DIR / "router_data.db"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("migration.savings")

def migrate():
    if not SAVINGS_JSON.exists():
        logger.warning("Arquivo savings.json não encontrado. Abortando migração.")
        return

    try:
        with open(SAVINGS_JSON, "r") as f:
            data = json.load(f)
        
        total_savings = data.get("total_savings", 0.0)
        logger.info(f"Dados lidos: ${total_savings:.4f} encontrados no JSON.")

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Criação da tabela de metadados do sistema para persistência KV
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        cursor.execute("INSERT OR REPLACE INTO system_metadata (key, value) VALUES (?, ?)", 
                       ("total_savings", str(total_savings)))
        
        # Verificação de integridade: Lê de volta do banco e compara com o valor original
        cursor.execute("SELECT value FROM system_metadata WHERE key = ?", ("total_savings",))
        row = cursor.fetchone()
        if row:
            db_value = float(row[0])
            if abs(db_value - total_savings) < 1e-9:
                logger.info("✅ Verificação de integridade passou: os valores coincidem perfeitamente.")
                SAVINGS_JSON.unlink()
                logger.info(f"🗑️ Arquivo legado {SAVINGS_JSON.name} removido com segurança.")
            else:
                logger.error(f"❌ Falha de integridade: Valor no DB ({db_value}) difere do JSON ({total_savings}). Arquivo original preservado.")

        conn.commit()
        conn.close()
        logger.info(f"✅ Migração concluída com sucesso para {DB_PATH}")
        
    except Exception as e:
        logger.error(f"❌ Falha na migração: {e}")

if __name__ == "__main__":
    migrate()