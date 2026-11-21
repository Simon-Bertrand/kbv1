"""Public diagnostics: do not expose SQL, DSNs or credentials."""
import psycopg

def database_diagnostic(error):
    if isinstance(error,psycopg.errors.UndefinedTable) or isinstance(error,psycopg.errors.UndefinedFunction):
        return {'code':'schema_incomplete','detail':'Schéma KM incomplet. Vérifier l’initialisation du volume PostgreSQL avec python3 -m scripts.ops.manage doctor.'}
    if isinstance(error,psycopg.errors.InsufficientPrivilege):
        return {'code':'database_permissions','detail':'Droits PostgreSQL insuffisants : vérifier le compte applicatif avec python3 -m scripts.ops.manage doctor.'}
    if isinstance(error,psycopg.OperationalError):
        return {'code':'database_connection','detail':'Connexion PostgreSQL indisponible : python3 -m scripts.ops.manage doctor puis python3 -m scripts.ops.manage logs postgres.'}
    return {'code':'database_unavailable','detail':'PostgreSQL indisponible : consulter python3 -m scripts.ops.manage doctor et python3 -m scripts.ops.manage logs postgres.'}
