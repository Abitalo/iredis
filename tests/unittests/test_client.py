import pytest
import redis
from unittest.mock import MagicMock


from iredis.client import Client
from iredis.completers import IRedisCompleter
from iredis.entry import Rainbow, prompt_message


@pytest.fixture
def completer():
    return IRedisCompleter()


@pytest.mark.parametrize(
    "_input, command_name, expect_args",
    [
        ("keys *", "keys", ["*"]),
        ("DEL abc foo bar", "DEL", ["abc", "foo", "bar"]),
        ("cluster info", "cluster info", []),
        ("CLUSTER failover FORCE", "CLUSTER failover", ["FORCE"]),
    ],
)
def test_send_command(_input, command_name, expect_args):
    client = Client("127.0.0.1", "6379", None)
    client.execute_command_and_read_response = MagicMock()
    next(client.send_command(_input, None))
    args, kwargs = client.execute_command_and_read_response.call_args
    assert args == (command_name, *expect_args)


def test_patch_completer():
    client = Client("127.0.0.1", "6379", None)
    completer = IRedisCompleter()
    client.pre_hook(
        "MGET foo bar hello world", "MGET", "foo bar hello world", completer
    )
    assert completer.key_completer.words == ["world", "hello", "bar", "foo"]
    assert completer.key_completer.words == ["world", "hello", "bar", "foo"]

    client.pre_hook("GET bar", "GET", "bar", completer)
    assert completer.key_completer.words == ["bar", "world", "hello", "foo"]


def test_get_server_verison_after_client(config):
    Client("127.0.0.1", "6379", None)
    assert config.version.startswith("5.")

    config.version = "Unknown"
    config.no_info = True
    Client("127.0.0.1", "6379", None)
    assert config.version == "Unknown"


def test_do_help(config):
    client = Client("127.0.0.1", "6379", None)
    config.version = "5.0.0"
    resp = client.do_help("SET")
    assert resp[10] == ("", "1.0.0 (Avaiable on your redis-server: 5.0.0)")
    config.version = "2.0.0"
    resp = client.do_help("cluster", "addslots")
    assert resp[10] == ("", "3.0.0 (Not avaiable on your redis-server: 2.0.0)")


def test_rainbow_iterator():
    "test color infinite iterator"
    original_color = Rainbow.color
    Rainbow.color = list(range(0, 3))
    assert list(zip(range(10), Rainbow())) == [
        (0, 0),
        (1, 1),
        (2, 2),
        (3, 1),
        (4, 0),
        (5, 1),
        (6, 2),
        (7, 1),
        (8, 0),
        (9, 1),
    ]
    Rainbow.color = original_color


def test_prompt_message(iredis_client, config):
    config.rainbow = False
    assert prompt_message(iredis_client) == "127.0.0.1:6379[15]> "

    config.rainbow = True
    assert prompt_message(iredis_client)[:3] == [
        ("#cc2244", "1"),
        ("#bb4444", "2"),
        ("#996644", "7"),
    ]


def test_on_connection_error_retry(iredis_client, config):
    config.retry_times = 1
    mock_connection = MagicMock()
    mock_connection.read_response.side_effect = [
        redis.exceptions.ConnectionError(
            "Error 61 connecting to 127.0.0.1:7788. Connection refused."
        ),
        "hello",
    ]
    original_connection = iredis_client.connection
    iredis_client.connection = mock_connection
    value = iredis_client.execute_command_and_read_response("None", "GET", ["foo"])
    assert value == "hello"  # be rendered

    mock_connection.disconnect.assert_called_once()
    mock_connection.connect.assert_called_once()

    iredis_client.connection = original_connection


def test_on_connection_error_retry_without_retrytimes(iredis_client, config):
    config.retry_times = 0
    mock_connection = MagicMock()
    mock_connection.read_response.side_effect = [
        redis.exceptions.ConnectionError(
            "Error 61 connecting to 127.0.0.1:7788. Connection refused."
        ),
        "hello",
    ]
    iredis_client.connection = mock_connection
    with pytest.raises(redis.exceptions.ConnectionError):
        iredis_client.execute_command_and_read_response("None", "GET", ["foo"])

    mock_connection.disconnect.assert_not_called()
    mock_connection.connect.assert_not_called()


def test_socket_keepalive(config):
    config.socket_keepalive = True
    from iredis.client import Client

    newclient = Client("127.0.0.1", "6379", 0)
    assert newclient.connection.socket_keepalive

    # keepalive off
    config.socket_keepalive = False

    newclient = Client("127.0.0.1", "6379", 0)
    assert not newclient.connection.socket_keepalive


def test_not_retry_on_authentication_error(iredis_client, config):
    config.retry_times = 2
    mock_connection = MagicMock()
    mock_connection.read_response.side_effect = [
        redis.exceptions.AuthenticationError("Authentication required."),
        "hello",
    ]
    iredis_client.connection = mock_connection
    with pytest.raises(redis.exceptions.ConnectionError):
        iredis_client.execute_command_and_read_response("None", "GET", ["foo"])


def test_auto_select_db_and_auth_for_reconnect(iredis_client, config):
    config.retry_times = 2
    config.raw = True
    next(iredis_client.send_command("select 2"))
    assert iredis_client.connection.db == 2

    resp = next(iredis_client.send_command("auth 123"))
    assert "Client sent AUTH, but no password is set" in resp
    assert iredis_client.connection.password is None

    next(iredis_client.send_command("config set requirepass 'abc'"))
    next(iredis_client.send_command("auth abc"))
    assert iredis_client.connection.password == "abc"
    next(iredis_client.send_command("config set requirepass ''"))


def test_split_shell_command(iredis_client, completer):
    assert iredis_client.split_command_and_pipeline(" get json | rg . ", completer) == (
        " get json ",
        "rg . ",
    )

    assert iredis_client.split_command_and_pipeline(
        """ get "json | \\" hello" | rg . """, completer
    ) == (""" get "json | \\" hello" """, "rg . ")


def test_running_with_pipeline(clean_redis, iredis_client, capfd, completer):
    clean_redis.set("foo", "hello \n world")
    with pytest.raises(StopIteration):
        next(iredis_client.send_command("get foo | grep w", completer))
    out, err = capfd.readouterr()
    assert out == " world\n"


def test_running_with_multiple_pipeline(clean_redis, iredis_client, capfd, completer):
    clean_redis.set("foo", "hello world\nhello iredis")
    with pytest.raises(StopIteration):
        next(
            iredis_client.send_command("get foo | grep hello | grep iredis", completer)
        )
    out, err = capfd.readouterr()
    assert out == "hello iredis\n"


def test_can_not_connect_on_startup(capfd):
    Client("localhost", "16111", 15)
    out, err = capfd.readouterr()
    assert "connecting to localhost:16111." in err
