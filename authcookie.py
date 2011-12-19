#
# Simple authenticated cookie services.
# Cookie values are authenticated against a hash value; the hash value
# is made unpredictable by prepending a secret to the cookie value before
# hashing it.
#
# The authenticated cookie looks like '<original-value>:<base64 hash>'.
# Note that this does *not* obscure the original value at all. This is
# deliberate; if you want to obscure an original value too, you must do
# so before authenticating it.
#
# Cookies must be objects that obey the Cookie module's interface.

# The hash is currently SHA1.
import hashlib

# For some reason the base-64 encoding always appends a newline.
# BITE ME.
def genHashVal(val, secret):
	hv = hashlib.sha1(secret + val)
	return hv.digest().encode("base64")[:-1]

def setCookie(cookie, name, val, secret):
	cookie[name] = val + ":" + genHashVal(val, secret)

# Return a pair of plain value and authenticator hash for a given
# bit of a cookie. This guarantees that either both parts of the
# tuple exist or both are None; you do not get partial results.
def splitCookie(cookie, name):
	# No cookie morsel by that name? Lose.
	if not name in cookie:
		return (None, None)
	# Try to find the ':'.
	rawval = cookie[name].value
	rpos = rawval.rfind(":")
	if rpos == -1:
		return (None, None)
	cauth = rawval[rpos+1:]
	cval = rawval[:rpos]
	# Reject ':nominal-hash' and 'nominal-val:'.
	if not (cauth and cval):
		return (None, None)
	else:
		return (cval, cauth)

def valFromCookie(cookie, name, secret):
	(cval, cauth) = splitCookie(cookie, name)
	# Either no cookie or not a valid cookie for us.
	if not (cval and cauth):
		return None
	# ... so we can just verify it straight.
	if cauth != genHashVal(cval, secret):
		return None
	else:
		return cval
