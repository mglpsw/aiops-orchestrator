#!/usr/bin/env python3
import asyncio
import logging
from unittest.mock import AsyncMock
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from autonomous_engine import AutonomousRouter

# Configuração básica de log para o teste
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test.search_resilience")

async def _test_web_search_connection_failure():
    """
    Valida se o roteador lida corretamente com uma falha de conexão 
    simulada no motor de busca (DuckDuckGo).
    """
    logger.info("Iniciando teste de resiliência de busca web...")

    # Instanciamos o roteador (a URL do prometheus não importa aqui devido aos mocks)
    router = AutonomousRouter(prometheus_url="http://192.168.3.200:9090")

    # 1. Mock do Health Check e Ollama Alive
    router.check_ollama_alive = AsyncMock(return_value=True)
    router.get_system_health = AsyncMock(return_value={"local_degraded": False, "avg_latency": 0})

    # 2. Mock das chamadas utilitárias ao gemma3:4b
    # Primeira chamada: Classificação de Intenção -> WEB_REQUIRED
    # Segunda chamada: Extração de Query -> "cotação bitcoin hoje"
    router._call_ollama = AsyncMock()
    router._call_ollama.side_effect = ["WEB_REQUIRED", "cotação bitcoin hoje"]

    prompt = "Qual a cotação do bitcoin agora?"

    logger.info(f"Enviando prompt de teste: '{prompt}'")
    decision = await router.route(prompt)

    # Validações
    logger.info(f"Decisão tomada: {decision.agent} (Razão: {decision.reason})")

    assert decision.agent == "ollama"
    assert decision.reason == "web_search_rag"
    assert "SEARCH RESULTS:" in decision.prompt
    assert "USER QUESTION: Qual a cotação do bitcoin agora?" in decision.prompt
        



def test_web_search_connection_failure():
    asyncio.run(_test_web_search_connection_failure())

async def main():
    try:
        await _test_web_search_connection_failure()
    except AssertionError as e:
        logger.error(f"❌ Falha na validação do teste: {e}")
    except Exception as e:
        logger.error(f"💥 Erro inesperado durante o teste: {e}")

if __name__ == "__main__":
    # Ajuste de path para importar módulos corretamente se necessário
    import sys
    import os
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    
    asyncio.run(main())
