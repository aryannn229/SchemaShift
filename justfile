set shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

default:
    @just --list

dev:
    docker compose up --build

test:
    ./scripts/dev.ps1 test

test-integration:
    ./scripts/dev.ps1 test-integration

lint:
    ./scripts/dev.ps1 lint

typecheck:
    ./scripts/dev.ps1 typecheck

evaluate:
    ./scripts/dev.ps1 evaluate

migrate:
    ./scripts/dev.ps1 migrate

seed-samples:
    ./scripts/dev.ps1 seed-samples

build:
    ./scripts/dev.ps1 build
