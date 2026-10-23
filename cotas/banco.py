"""SQLite com limites, folhas extras, histórico e senha.

O saldo é a soma das folhas liberadas hoje; por isso ele "renova" sozinho à meia-noite.
"""
import datetime as dt
import hashlib
import hmac
import os
import sqlite3
import threading
from contextlib import contextmanager

from . import config

LIBERADAS = ("liberado", "isento", "desativado")
ITERACOES = 200_000
_lock = threading.Lock()

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (usuario TEXT PRIMARY KEY, limite INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS extras (usuario TEXT, data TEXT, folhas INTEGER,
                                   PRIMARY KEY (usuario, data));
CREATE TABLE IF NOT EXISTS trabalhos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chave TEXT UNIQUE NOT NULL,
    usuario TEXT NOT NULL, impressora TEXT, titulo TEXT, folhas INTEGER NOT NULL,
    situacao TEXT NOT NULL, motivo TEXT, data TEXT NOT NULL, hora TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_usuario_data ON trabalhos (usuario, data);
CREATE TABLE IF NOT EXISTS estado (chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
"""


def normalizar(usuario):
    """'DOMINIO\\Joao' ou 'Joao@escola' -> 'joao'."""
    return (usuario or "").strip().split("\\")[-1].split("@")[0].lower()


def hoje():
    return dt.date.today().isoformat()


@contextmanager
def conectar():
    c = sqlite3.connect(config.ARQ_BANCO, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def inicializar():
    with conectar() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(ESQUEMA)


def _um(c, sql, args, padrao=None):
    row = c.execute(sql, args).fetchone()
    return row[0] if row and row[0] is not None else padrao


def _get(c, chave, padrao=None):
    return _um(c, "SELECT valor FROM estado WHERE chave=?", (chave,), padrao)


def _set(c, chave, valor):
    c.execute("INSERT OR REPLACE INTO estado VALUES (?, ?)", (chave, str(valor)))


# ---------------------------------------------------------------- saldo

def _saldo(c, usuario, cfg):
    limite = _um(c, "SELECT limite FROM usuarios WHERE usuario=?", (usuario,),
                 int(cfg["limite_diario_padrao"]))
    extra = _um(c, "SELECT folhas FROM extras WHERE usuario=? AND data=?", (usuario, hoje()), 0)
    usados = _um(c, "SELECT SUM(folhas) FROM trabalhos WHERE usuario=? AND data=? "
                    "AND situacao='liberado'", (usuario, hoje()), 0)
    amanha = dt.date.today() + dt.timedelta(days=1)
    return {
        "usuario": usuario,
        "isento": usuario in {normalizar(u) for u in cfg["usuarios_isentos"]},
        "limite": limite, "extra": extra, "total": limite + extra, "usados": usados,
        "restantes": max(0, limite + extra - usados),
        "renova_em": amanha.strftime("%d/%m") + " às 00:00",
    }


def saldo(usuario, cfg=None):
    with conectar() as c:
        return _saldo(c, normalizar(usuario), cfg or config.carregar())


def ultimos(usuario, n=8):
    with conectar() as c:
        rows = c.execute("SELECT id, titulo, folhas, situacao, motivo, data, hora FROM trabalhos "
                         "WHERE usuario=? ORDER BY id DESC LIMIT ?", (normalizar(usuario), n))
        return [dict(r) for r in rows]


def situacao(chave):
    with conectar() as c:
        return _um(c, "SELECT situacao FROM trabalhos WHERE chave=?", (chave,))


def decidir(chave, usuario, impressora, titulo, folhas, cfg):
    """Registra e devolve (liberar, situacao, motivo). folhas=None: não deu para contar."""
    usuario = normalizar(usuario)
    with _lock, conectar() as c:
        # Trava o banco: dois trabalhos ao mesmo tempo não podem gastar o mesmo saldo
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT situacao, motivo FROM trabalhos WHERE chave=?", (chave,)).fetchone()
        if row:
            return row["situacao"] in LIBERADAS, row["situacao"], row["motivo"] or ""

        s = _saldo(c, usuario, cfg)
        politica = cfg["paginas_desconhecidas"]
        desconhecido = folhas is None
        if desconhecido:
            folhas = 1 if politica == "contar_1" else 0
        motivo = ""

        if _get(c, "controle_ativo", "1") == "0":
            sit = "desativado"
        elif s["isento"]:
            sit = "isento"
        elif desconhecido and politica == "bloquear":
            sit, motivo = "bloqueado", "Não foi possível contar as páginas deste documento."
        elif folhas <= s["restantes"]:
            sit = "liberado"
        else:
            sit = "bloqueado"
            motivo = f"Precisava de {folhas} folha(s), mas restavam {s['restantes']}."

        agora = dt.datetime.now()
        c.execute("INSERT INTO trabalhos (chave, usuario, impressora, titulo, folhas, situacao, "
                  "motivo, data, hora) VALUES (?,?,?,?,?,?,?,?,?)",
                  (chave, usuario, impressora, (titulo or "")[:200], folhas, sit, motivo,
                   agora.date().isoformat(), agora.strftime("%H:%M:%S")))
        return sit in LIBERADAS, sit, motivo


# ---------------------------------------------------------------- administração

def definir_limite(usuario, limite):
    with conectar() as c:
        c.execute("INSERT OR REPLACE INTO usuarios VALUES (?, ?)", (normalizar(usuario), int(limite)))


def remover_limite(usuario):
    with conectar() as c:
        c.execute("DELETE FROM usuarios WHERE usuario=?", (normalizar(usuario),))


def adicionar_extra(usuario, folhas):
    with conectar() as c:
        c.execute("INSERT INTO extras VALUES (?, ?, ?) ON CONFLICT(usuario, data) "
                  "DO UPDATE SET folhas = folhas + excluded.folhas",
                  (normalizar(usuario), hoje(), int(folhas)))


def estornar_hoje(usuario):
    """Devolve as folhas usadas hoje, mantendo o histórico."""
    with conectar() as c:
        return c.execute("UPDATE trabalhos SET situacao='estornado' WHERE usuario=? AND data=? "
                         "AND situacao='liberado'", (normalizar(usuario), hoje())).rowcount


def limites_proprios():
    with conectar() as c:
        return {r[0]: r[1] for r in c.execute("SELECT usuario, limite FROM usuarios ORDER BY 1")}


def usuarios_conhecidos():
    with conectar() as c:
        return [r[0] for r in c.execute(
            "SELECT usuario FROM usuarios UNION SELECT usuario FROM trabalhos ORDER BY 1")]


def historico(usuario=None, dias=7):
    desde = (dt.date.today() - dt.timedelta(days=dias - 1)).isoformat()
    sql = "SELECT data, hora, usuario, titulo, folhas, situacao FROM trabalhos WHERE data >= ?"
    args = [desde]
    if usuario:
        sql += " AND usuario=?"
        args.append(normalizar(usuario))
    with conectar() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY id DESC", args)]


# ---------------------------------------------------------------- senha e liga/desliga
# A senha não é guardada: só o hash PBKDF2 com um sal aleatório.

def _hash(senha, sal, iteracoes=ITERACOES):
    return hashlib.pbkdf2_hmac("sha256", senha.encode(), sal, iteracoes).hex()


def definir_senha(senha, substituir=False):
    sal = os.urandom(16)
    valor = f"{ITERACOES}${sal.hex()}${_hash(senha, sal)}"
    with _lock, conectar() as c:
        c.execute("BEGIN IMMEDIATE")
        if _get(c, "senha") and not substituir:
            return False
        _set(c, "senha", valor)
        return True


def conferir_senha(senha):
    with conectar() as c:
        guardada = _get(c, "senha")
    try:
        iteracoes, sal, esperado = guardada.split("$")[-3:]
        return hmac.compare_digest(_hash(senha, bytes.fromhex(sal), int(iteracoes)), esperado)
    except (AttributeError, ValueError):
        return False


def definir_controle(ativo):
    with conectar() as c:
        _set(c, "controle_ativo", int(ativo))
        _set(c, "controle_alterado_em", dt.datetime.now().strftime("%d/%m %H:%M"))


def estado():
    with conectar() as c:
        return {"controle_ativo": _get(c, "controle_ativo", "1") == "1",
                "controle_alterado_em": _get(c, "controle_alterado_em", ""),
                "senha_definida": _get(c, "senha") is not None}
