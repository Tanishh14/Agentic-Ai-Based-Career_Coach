"""Library → skill map used by import-usage detectors and the dependency parser."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Lib:
    key: str
    skill_key: str
    label: str
    py: tuple[str, ...] = ()
    js: tuple[str, ...] = ()  # exact package names or prefixes ending with '/'
    java: tuple[str, ...] = ()
    go: tuple[str, ...] = ()
    deps: tuple[str, ...] = field(default_factory=tuple)  # manifest dependency names


LIBS: list[Lib] = [
    Lib("react", "react", "React", js=("react", "react-dom", "react-native"), deps=("react", "react-native")),
    Lib("nextjs", "nextjs", "Next.js", js=("next", "next/"), deps=("next",)),
    Lib("vue", "vue", "Vue", js=("vue", "nuxt", "pinia"), deps=("vue", "nuxt")),
    Lib("angular", "angular", "Angular", js=("@angular/",), deps=("@angular/core",)),
    Lib("express", "express", "Express", js=("express", "fastify", "koa", "@hono/", "hono"), deps=("express", "fastify", "koa", "hono")),
    Lib("express_node", "nodejs", "Node.js server/runtime APIs",
        js=("express", "fastify", "koa", "http", "node:http", "fs", "node:fs", "path", "node:path", "child_process", "node:child_process",
            "events", "stream", "worker_threads", "node:crypto", "crypto", "os", "node:os"),
        deps=("express", "fastify", "koa", "nodemon", "ts-node", "tsx")),
    Lib("nestjs", "nestjs", "NestJS", js=("@nestjs/",), deps=("@nestjs/core",)),
    Lib("django", "django", "Django", py=("django", "rest_framework"), deps=("django", "djangorestframework")),
    Lib("flask", "flask", "Flask", py=("flask",), deps=("flask",)),
    Lib("fastapi", "fastapi", "FastAPI", py=("fastapi",), deps=("fastapi",)),
    Lib("spring", "spring", "Spring", java=("org.springframework",), deps=("spring-boot-starter", "spring-boot-starter-web", "org.springframework.boot")),
    Lib("tailwind", "tailwind", "Tailwind CSS", js=("tailwindcss",), deps=("tailwindcss", "@tailwindcss/vite", "@tailwindcss/postcss")),
    Lib("graphql", "graphql", "GraphQL", py=("graphene", "strawberry", "ariadne"),
        js=("graphql", "@apollo/", "apollo-server", "graphql-yoga", "type-graphql", "urql"), deps=("graphql", "@apollo/server", "graphene", "strawberry-graphql", "ariadne")),
    Lib("pandas", "pandas", "pandas/NumPy", py=("pandas", "numpy", "polars"), deps=("pandas", "numpy", "polars")),
    Lib("ml", "machine_learning", "ML library", py=("sklearn", "torch", "tensorflow", "keras", "xgboost", "lightgbm", "catboost"),
        js=("@tensorflow/",), deps=("scikit-learn", "torch", "tensorflow", "keras", "xgboost", "lightgbm")),
    Lib("llm", "llm_apps", "LLM SDK", py=("openai", "anthropic", "langchain", "langchain_core", "langchain_openai", "langchain_community",
                                         "llama_index", "ollama", "groq", "litellm", "google.generativeai", "google.genai", "langgraph"),
        js=("openai", "@anthropic-ai/", "langchain", "@langchain/", "ai", "@ai-sdk/", "ollama", "groq-sdk", "@google/generative-ai"),
        deps=("openai", "anthropic", "langchain", "llama-index", "ollama", "groq", "litellm", "@anthropic-ai/sdk", "@langchain/core", "langgraph")),
    Lib("nosql", "nosql", "NoSQL client", py=("pymongo", "motor", "mongoengine", "cassandra", "firebase_admin", "beanie"),
        js=("mongoose", "mongodb", "firebase", "firebase-admin", "@aws-sdk/client-dynamodb"), java=("org.springframework.data.mongodb", "com.mongodb"),
        go=("go.mongodb.org/mongo-driver",), deps=("pymongo", "motor", "mongoose", "mongodb", "firebase-admin", "mongoengine")),
    Lib("mq", "message_queues", "message-queue client", py=("kafka", "confluent_kafka", "aiokafka", "pika", "celery", "rq", "dramatiq", "aio_pika"),
        js=("kafkajs", "amqplib", "bullmq", "bull", "@google-cloud/pubsub"), java=("org.apache.kafka", "org.springframework.kafka", "org.springframework.amqp"),
        go=("github.com/segmentio/kafka-go", "github.com/rabbitmq/amqp091-go"), deps=("celery", "kafka-python", "confluent-kafka", "pika", "kafkajs", "amqplib", "bullmq")),
]

# Dependencies that indicate a skill without any code-usage proof (dependency_detected only).
DEP_ONLY: dict[str, tuple[str, ...]] = {
    "sql": ("sqlalchemy", "psycopg2", "psycopg2-binary", "psycopg", "asyncpg", "pymysql", "mysqlclient", "aiosqlite", "sqlmodel", "alembic",
            "pg", "mysql2", "sqlite3", "better-sqlite3", "prisma", "@prisma/client", "knex", "sequelize", "typeorm", "drizzle-orm",
            "spring-boot-starter-data-jpa", "gorm.io/gorm", "github.com/lib/pq", "github.com/jackc/pgx", "peewee", "tortoise-orm"),
    "automated_testing": ("pytest", "pytest-asyncio", "jest", "vitest", "mocha", "chai", "@testing-library/react", "cypress", "playwright",
                          "@playwright/test", "supertest", "junit", "junit-jupiter", "spring-boot-starter-test", "github.com/stretchr/testify", "rspec", "phpunit"),
    "auth_security": ("pyjwt", "python-jose", "passlib", "bcrypt", "flask-login", "django-allauth", "authlib", "jsonwebtoken", "passport",
                      "next-auth", "@auth/core", "bcryptjs", "argon2", "helmet", "spring-boot-starter-security", "github.com/golang-jwt/jwt"),
    "caching": ("redis", "ioredis", "aioredis", "cachetools", "node-cache", "lru-cache", "django-redis", "flask-caching"),
    "observability": ("sentry-sdk", "@sentry/node", "@sentry/react", "prometheus-client", "prom-client", "opentelemetry-sdk", "@opentelemetry/sdk-node",
                      "structlog", "winston", "pino", "loguru"),
    "aws": ("boto3", "aws-sdk", "@aws-sdk/client-s3", "aws-cdk-lib", "aws-cdk"),
    "gcp": ("google-cloud-storage", "@google-cloud/storage", "firebase-admin", "google-cloud-bigquery"),
    "azure": ("azure-storage-blob", "@azure/storage-blob", "azure-identity"),
}


def lib_for_js(module: str) -> list[Lib]:
    hits = []
    for lib in LIBS:
        for m in lib.js:
            if (m.endswith("/") and module.startswith(m)) or module == m or module.startswith(m + "/"):
                hits.append(lib)
                break
    return hits


def skills_for_dependency(dep: str) -> list[str]:
    d = dep.lower().strip()
    out = [lib.skill_key for lib in LIBS if d in (x.lower() for x in lib.deps)]
    for skill, names in DEP_ONLY.items():
        if d in names:
            out.append(skill)
    return sorted(set(out))
