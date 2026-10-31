"""Praj.AI Onboarding Converter — HTTP microservice around the raw->Golden engine.

PRAJ_IQ calls this service to turn a customer's raw POS/accounting uploads into the
Golden Standard workbook, then feeds that workbook into its existing golden intake.

Nothing in here is POS-specific; it only wraps engine/ (ingest, build, reconcile, write).
"""
