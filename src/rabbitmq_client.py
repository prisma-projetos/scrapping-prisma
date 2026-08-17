from __future__ import annotations

import json
import os
from dataclasses import dataclass

import pika
from pika.exceptions import AMQPError, NackError, UnroutableError

# Nomes confirmados pelo time de DevOps, que cria e mantém a topologia
# (exchange, fila e dead-letter queue) diretamente na configuração do
# RabbitMQ -- este publisher NÃO declara nada, só conecta e publica (ver
# README, seção "Publisher (RabbitMQ)"). Exchange tipo `direct`, durável --
# nenhum dos dois muda.
#
# A ROUTING KEY usada no binding entre a exchange e a fila ainda NÃO foi
# confirmada pelo DevOps. O default abaixo (nome da fila) é só uma hipótese
# razoável, não um valor fechado -- confirme antes de depender disso em
# produção, e ajuste via RABBITMQ_ROUTING_KEY sem precisar mexer em código.
DEFAULT_QUEUE = "scraper.dados.queue"
DEFAULT_EXCHANGE = "scraper.entrada.exchange"
DEFAULT_EXCHANGE_TYPE = "direct"


class PublishError(Exception):
    """Falha ao conectar ou publicar no RabbitMQ. `jobs_publisher` captura
    isso e delega para `fail_job` (retry já existente em `jobs_store.py`)
    -- não derruba a execução inteira por causa de um job."""


@dataclass(frozen=True)
class RabbitMQConfig:
    host: str
    port: int
    user: str
    password: str
    vhost: str
    queue: str
    exchange: str
    # Não é usado para declarar nada (o publisher não declara topologia --
    # ver docstring de RabbitMQPublisher). Mantido só como documentação do
    # tipo já confirmado pelo DevOps (`direct`), caso seja útil depois
    # (ex.: um teste de integração real, ou se a decisão de não declarar
    # for revista).
    exchange_type: str
    routing_key: str
    durable: bool

    @classmethod
    def from_env(cls) -> "RabbitMQConfig":
        queue = os.environ.get("RABBITMQ_QUEUE", DEFAULT_QUEUE)
        durable = os.environ.get("RABBITMQ_DURABLE", "true").strip().lower() not in ("false", "0", "no", "")
        return cls(
            host=os.environ.get("RABBITMQ_HOST", "localhost"),
            port=int(os.environ.get("RABBITMQ_PORT", "5672")),
            user=os.environ.get("RABBITMQ_USER", "guest"),
            password=os.environ.get("RABBITMQ_PASSWORD", "guest"),
            vhost=os.environ.get("RABBITMQ_VHOST", "/"),
            queue=queue,
            exchange=os.environ.get("RABBITMQ_EXCHANGE", DEFAULT_EXCHANGE),
            exchange_type=os.environ.get("RABBITMQ_EXCHANGE_TYPE", DEFAULT_EXCHANGE_TYPE),
            routing_key=os.environ.get("RABBITMQ_ROUTING_KEY", queue),
            durable=durable,
        )


class RabbitMQPublisher:
    """Wrapper fino e síncrono sobre `pika.BlockingConnection` -- mesmo
    estilo síncrono do resto do projeto (`requests`, `psycopg`). Abre uma
    conexão por execução do publisher (script roda uma vez e sai, ver
    `src/publish_jobs.py`) e publica mensagens JSON persistentes quando a
    fila é durável.

    NÃO declara exchange, fila nem faz bind -- a topologia (exchange, fila
    e dead-letter queue) é criada e mantida pelo time de DevOps direto na
    configuração do RabbitMQ. Declarar aqui com argumentos que não batem
    exatamente com o que já existe no broker (ex.: argumentos de
    dead-letter na fila) faz o RabbitMQ rejeitar a conexão com
    `PRECONDITION_FAILED` -- por isso o publisher assume que a topologia já
    existe e só conecta + publica.

    Uso:
        with RabbitMQPublisher() as publisher:
            publisher.publish(payload)
    """

    def __init__(self, config: RabbitMQConfig | None = None):
        self._config = config or RabbitMQConfig.from_env()
        self._connection: pika.BlockingConnection | None = None
        self._channel = None

    def __enter__(self) -> "RabbitMQPublisher":
        try:
            self._connect()
        except PublishError:
            self.close()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _connect(self) -> None:
        cfg = self._config
        try:
            credentials = pika.PlainCredentials(cfg.user, cfg.password)
            parameters = pika.ConnectionParameters(
                host=cfg.host,
                port=cfg.port,
                virtual_host=cfg.vhost,
                credentials=credentials,
            )
            self._connection = pika.BlockingConnection(parameters)
            self._channel = self._connection.channel()

            # Sem queue_declare/exchange_declare/queue_bind: a topologia já
            # existe, criada pelo DevOps (ver docstring da classe). Ativa
            # publisher confirms -- o broker confirma (ack) ou recusa
            # (nack) cada mensagem publicada, então `publish()` sabe se a
            # mensagem realmente chegou em vez de assumir sucesso só por
            # não ter dado exceção de conexão.
            self._channel.confirm_delivery()
        except AMQPError as exc:
            raise PublishError(f"Falha ao conectar ao RabbitMQ: {exc}") from exc

    def publish(self, payload: dict) -> None:
        if self._channel is None:
            raise PublishError("RabbitMQPublisher usado fora do context manager (canal não aberto)")
        cfg = self._config
        try:
            self._channel.basic_publish(
                exchange=cfg.exchange,
                routing_key=cfg.routing_key,
                body=json.dumps(payload, default=str).encode("utf-8"),
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=2 if cfg.durable else 1,
                ),
                # mandatory=True: se não houver fila com binding correspondente
                # à routing key, o broker devolve a mensagem (Basic.Return) em
                # vez de descartá-la silenciosamente. Combinado com
                # confirm_delivery() acima, isso vira UnroutableError abaixo.
                mandatory=True,
            )
        except UnroutableError as exc:
            # Mensagem não roteada para nenhuma fila -- routing key errada
            # ou binding inexistente/diferente do esperado (ver aviso sobre
            # routing key ainda não confirmada, no topo do arquivo).
            raise PublishError(f"Mensagem não roteada no RabbitMQ (routing key/binding incorretos?): {exc}") from exc
        except NackError as exc:
            # Broker recebeu a mensagem mas recusou (nack) -- geralmente
            # problema interno do broker (ex.: fila cheia, sem espaço em
            # disco), não do payload em si.
            raise PublishError(f"Mensagem recusada (nack) pelo RabbitMQ: {exc}") from exc
        except AMQPError as exc:
            raise PublishError(f"Falha ao publicar mensagem no RabbitMQ: {exc}") from exc

    def close(self) -> None:
        if self._connection is not None and self._connection.is_open:
            self._connection.close()
        self._connection = None
        self._channel = None
