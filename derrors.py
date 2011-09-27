#
# Our global collection of cross-module errors.

# Everyone is a kid of WikiErr.
class WikiErr(Exception):
	pass

class IntErr(WikiErr):
	# Internal errors.
	pass

class CacheKeyErr(IntErr):
	# Key errors in the cache interface.
	pass

class IOErr(WikiErr):
	# IO error during operations
	pass

class CfgErr(WikiErr):
	# Configuration has ... issues
	pass

class RendErr(WikiErr):
	# Fatal error during rendering.
	pass

class AuthErr(WikiErr):
	# Authentication problem in the innards.
	pass

class ReqErr(WikiErr):
	# Processing error in the request.
	# This is an out of band internal error indicating that
	# the HTTP request is garbled or broken and should be
	# aborted.
	pass
