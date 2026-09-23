# Testes da distribuição

Execute a partir da pasta extraída da entrega:

```bash
python -B -m unittest discover -s pack/tests -v
```

Os testes só usam diretórios temporários e repositórios Git sintéticos. Não usam rede, provider, banco ou credenciais. `jsonschema` é dependência de desenvolvimento; ausência da dependência é erro explícito, não skip que aparente sucesso.

Positivos e negativos verificam manifest, estrutura do registro, templates e instalação. Não são mutation testing completo do método, teste clínico ou qualificação de um repositório real. Suíte verde não basta para declarar independência semântica.
