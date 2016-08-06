from zope.interface import implementer
from twisted.trial import unittest
from twisted.web import server
from nevow.inevow import IRequest
from allmydata.web import common

# XXX FIXME when we introduce "mock" as a dependency, these can
# probably just be Mock instances
@implementer(IRequest)
class FakeRequest(object):
    def __init__(self):
        self.method = "POST"
        self.fields = dict()
        self.args = dict()


class FakeField(object):
    def __init__(self, *values):
        if len(values) == 1:
            self.value = values[0]
        else:
            self.value = list(values)


class FakeClientWithToken(object):
    token = 'a' * 32

    def get_auth_token(self):
        return self.token


class TestTokenOnlyApi(unittest.TestCase):

    def setUp(self):
        self.client = FakeClientWithToken()
        self.page = common.TokenOnlyWebApi(self.client)

    def test_not_post(self):
        req = FakeRequest()
        req.method = "GET"

        self.assertRaises(
            server.UnsupportedMethod,
            self.page.render, req,
        )

    def test_missing_token(self):
        req = FakeRequest()

        exc = self.assertRaises(
            common.WebError,
            self.page.render, req,
        )
        self.assertEquals(exc.text, "Missing token")
        self.assertEquals(exc.code, 401)

    def test_token_in_get_args(self):
        req = FakeRequest()
        req.args['token'] = 'z' * 32

        exc = self.assertRaises(
            common.WebError,
            self.page.render, req,
        )
        self.assertEquals(exc.text, "Do not pass 'token' as URL argument")
        self.assertEquals(exc.code, 400)

    def test_invalid_token(self):
        wrong_token = 'b' * 32
        req = FakeRequest()
        req.fields['token'] = FakeField(wrong_token)

        exc = self.assertRaises(
            common.WebError,
            self.page.render, req,
        )
        self.assertEquals(exc.text, "Invalid token")
        self.assertEquals(exc.code, 401)

    def test_valid_token_no_t_arg(self):
        req = FakeRequest()
        req.fields['token'] = FakeField(self.client.token)

        with self.assertRaises(common.WebError) as exc:
            self.page.render(req)
        self.assertEquals(exc.exception.text, "Must provide 't=' argument")
        self.assertEquals(exc.exception.code, 400)

    def test_valid_token_invalid_t_arg(self):
        req = FakeRequest()
        req.fields['token'] = FakeField(self.client.token)
        req.args['t'] = 'not at all json'

        with self.assertRaises(common.WebError) as exc:
            self.page.render(req)
        self.assertTrue("invalid type" in exc.exception.text)
        self.assertEquals(exc.exception.code, 400)

    def test_valid(self):
        req = FakeRequest()
        req.fields['token'] = FakeField(self.client.token)
        req.args['t'] = ['json']

        result = self.page.render(req)
        self.assertTrue(result == NotImplemented)
