"""BIT unified sign-in (sso.bit.edu.cn) for a Yanhekt session."""

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, quote, urljoin, urlsplit

import keyring
from bs4 import BeautifulSoup
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad, unpad
from keyring.errors import KeyringError

from client import CONFIG, ORIGIN

SSO = 'https://sso.bit.edu.cn'
KEYRING = 'yanhekit'
USERNAME = CONFIG / 'username'
# Seals the AES key for the phone-number lookup; taken from the SSO page script.
SSO_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAjVr1zKwohU3xA0afprWLSQvIymaSH/V27MedFc+CecXSnORIFMAp4uEIb4taDq/2X4eMeTI66Mu/rB5GKSFDbExF2Gu4NaO/CNDpf1gHMScUrIFCh4CDqzBnx17kclvezLkIK0T8FVa4cRsINvzjbnA6jUSMaf6Fm1n9wTAtW6QYBjssGOEtCj+c38PTBdFMmJbXp3brt1tEBesz6lb3Fjp76FGvDZ08xtYG8fxYPuiMwKU04eS+mcX/BunwgpU3zwekHYB+PWRIvq0lBry9Wms25sJE5T/RAv5fEuMLbBkfcZK3+7ivSZthTmPpr2Ap/ji70ZZ6u2jvR5VJq+LJHQIDAQAB
-----END PUBLIC KEY-----"""


class SignInError(Exception):
  """A failure to show to the user as is."""


def saved_username():
  return USERNAME.read_text(encoding='utf-8').strip() if USERNAME.exists() else ''


def saved_password(username):
  try:
    return keyring.get_password(KEYRING, username) if username else None
  except KeyringError:
    return None


def encrypt(text, key):
  return base64.b64encode(AES.new(key, AES.MODE_ECB).encrypt(pad(text.encode(), 16))).decode()


def decrypt(text, key):
  return unpad(AES.new(key, AES.MODE_ECB).decrypt(base64.b64decode(text)), 16).decode()


class Page:
  """A CAS page; the values it needs are the text of elements with known ids."""

  def __init__(self, response):
    self.url = response.url
    self.soup = BeautifulSoup(response.text, 'html.parser')
    forms = self.soup.find_all('form', action=True)
    self.action = urljoin(response.url, forms[-1]['action']) if forms else response.url

  def __getitem__(self, id):
    tag = self.soup.find(id=id)
    return tag.get_text(strip=True) if tag else ''


class SignIn:
  """The page's password, image captcha and SMS steps; each returns True once Yanhekt accepts the session.

  `publish(**values)` receives what the page shows: stage (credentials or verify), captcha (an image data
  URL), phone (masked), sending and resend_at.
  """

  def __init__(self, client, publish):
    self.client, self.publish = client, publish
    self.page = None
    self.username = self.secret = self.phone = ''
    self.remember = True
    self.resend_at = 0

  def request(self, method, url, **kwargs):
    headers = kwargs.pop('headers', {})
    if self.page:
      headers['Referer'] = self.page.url
    if method == 'POST':
      headers['Origin'] = SSO
    if '/protected/' in url:
      key = secrets.token_hex(16)
      encoded = base64.b64encode(key.encode()).decode()
      middle = len(encoded) // 2
      value = hashlib.md5((encoded[:middle] + encoded + encoded[middle:]).encode()).hexdigest()
      headers.update({'Csrf-Key': key, 'Csrf-Value': value, 'Sid-Language': 'zh_CN'})
    # CAS answers a rejected form with an error status and a page to parse, so statuses are not raised.
    return self.client.session.request(method, url, headers=headers, timeout=30, **kwargs)

  def start(self):
    """Sign in with the remembered SSO session, then the saved password, as far as they go without the user."""
    if self.open():
      return True
    username = saved_username()
    return self.password(username, '') if saved_password(username) else False

  def open(self):
    """Follow Yanhekt's CAS redirect to the password form; a remembered SSO session signs in instead."""
    self.page = None
    params = {'r': 'auth/login', 'auType': '', 'tenant_code': 21, 'forward': ORIGIN + '/course'}
    self.request('GET', ORIGIN + '/casapi/index.php', params=params, allow_redirects=False)
    params['auType'] = 'cas'
    response = self.request('GET', ORIGIN + '/casapi/index.php', params=params, allow_redirects=False)
    service = parse_qs(urlsplit(response.headers['Location']).query)['service'][0]
    response = self.request('GET', SSO + '/cas/login', params={'service': service})
    if self.client.authenticate():
      return True
    self.page = Page(response)
    if not self.page['login-croypto']:
      raise SignInError('无法识别统一身份认证页面，请稍后重试')
    return False

  def password(self, username, password, captcha='', remember=True):
    password = password or saved_password(username)
    if not username or not password:
      raise SignInError('请输入学号和密码')
    # Without a password form, or back from the SMS step, start over; a remembered SSO session signs in at once.
    if (not self.page or self.page['user-object-id']) and self.open():
      return True
    self.username, self.secret, self.remember = username, password, remember
    key = base64.b64decode(self.page['login-croypto'])
    url = f'{SSO}/cas/api/protected/user/findCaptchaCount/{quote(username, safe="")}'
    info = self.request('GET', url).json()['data']
    if info.get('captchaInvisible') and not captcha:
      url = info['captchaUrl']
      if not url.startswith('data:'):
        image = self.request('GET', urljoin(SSO + '/cas/', url)).content
        url = 'data:image/png;base64,' + base64.b64encode(image).decode()
      self.publish(stage='credentials', captcha=url)
      return False
    form = {
      'type': 'UsernamePassword',
      '_eventId': 'submit',
      'geolocation': '',
      'execution': self.page['login-page-flowkey'],
      'username': username,
      'croypto': self.page['login-croypto'],
      'password': encrypt(password, key),
      'captcha_code': captcha,
      'captcha_payload': encrypt(json.dumps(info.get('captchaPayload', {}), separators=('.', ':')), key),
    }
    self.page = Page(self.request('POST', self.page.action, data=form))
    if self.page['user-object-id']:
      self.send_code(stage='verify', captcha=None, phone=self.lookup_phone())
      return False
    if not self.client.authenticate():
      raise SignInError('学号或密码错误')
    self.keep_credentials()
    return True

  def lookup_phone(self):
    """Keep the bound number for the SMS step, which the SSO only returns encrypted, and return it masked."""
    key = secrets.token_bytes(16)
    sealed = PKCS1_v1_5.new(RSA.import_key(SSO_PUBLIC_KEY)).encrypt(base64.b64encode(key))
    response = self.request(
      'POST',
      SSO + '/cas/api/protected/sms/getPhoneNumberByUserId',
      data=encrypt(json.dumps({'userId': self.page['user-object-id']}, separators=('.', ':')), key),
      headers={
        'Content-Type': 'application/json',
        'hasCrypto': 'true',
        'privateKey': base64.b64encode(sealed).decode(),
      },
    )
    payload = response.text
    if payload.startswith('"'):
      payload = json.loads(payload)
    data = json.loads(decrypt(payload, key))['data']
    self.phone = data['tel']
    return data.get('maskTel', '')

  def send_code(self, **values):
    """Send an SMS code at most once a minute, publishing `values` along with the sending state."""
    if time.time() < self.resend_at:
      raise SignInError('请稍后再发送')
    self.publish(sending=True, **values)
    try:
      body = {'phone': self.phone, 'businessNo': '0008'}
      result = self.request('POST', SSO + '/cas/api/protected/sms/publicNoToken/sendSmsCode', json=body).json()
      if result['code'] != 200:
        raise SignInError('验证码发送失败，请稍后重试')
      self.resend_at = time.time() + 60
    finally:
      self.publish(sending=False, resend_at=self.resend_at)

  def verify(self, code):
    result = self.request(
      'POST',
      SSO + '/cas/api/protected/sms/checkToken',
      json={'phone': self.phone, 'token': code, 'delete': False, 'trustDevice': True},
    ).json()
    if result['code'] != 200:
      raise SignInError('验证码错误，请重新输入')
    form = {
      'username': self.username,
      'password': code,
      'type': 'smsLogin',
      '_eventId': 'submit',
      'geolocation': '',
      'execution': self.page['login-page-flowkey'],
      'captcha_code': '',
      'trustDevice': 'true',
    }
    self.request('POST', self.page.action, data=form)
    if not self.client.authenticate():
      raise SignInError('登录未完成，请重新登录')
    self.keep_credentials()
    return True

  def reset(self):
    """Return to the password form."""
    self.page = None
    self.resend_at = 0
    self.publish(stage='credentials', phone='', sending=False, resend_at=0)

  def keep_credentials(self):
    try:
      if self.remember:
        keyring.set_password(KEYRING, self.username, self.secret)
        USERNAME.write_text(self.username + '\n', encoding='utf-8')
      elif saved_password(self.username):
        keyring.delete_password(KEYRING, self.username)
        USERNAME.unlink(missing_ok=True)
    except KeyringError as error:
      print(f'Keychain unavailable, credentials not saved: {error}', flush=True)
