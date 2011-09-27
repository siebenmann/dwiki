#
# Serve static files out of a portion of the URL space managed by a
# DWiki instance.
# We do not serve directories at all, only files.

import os.path
import mimetypes

import httputil, htmlresp

# Attempt to discover the content-type of a random file based on its
# extension. (Cue ominious music.)
contentTypes = mimetypes.types_map.copy()
contentTypes.update({
	'.py': "text/plain", '.c': "text/plain", '.h': "text/plain",
	})
def guessContentType(path):
	path = path.lower()
	base, ext = os.path.splitext(path)
	if ext and ext in contentTypes:
		return contentTypes[ext]
	# Default? Uh. Good question.
	return "application/octet-stream"

# Return the relative path of a static request, '' if it is
# looking for the root, and None otherwise.
def getStaticPath(cfg, reqdata):
	return httputil.getRelativePath(cfg['staticurl'],
					reqdata['request-fullpath'])

# This shows how to serve static data out of a subpart of the
# Wiki path. To do it best, serve a URL/path scheme that the
# Wiki will never use (eg, start the URL with a dot, or use
# RCS, or the like).
# NOTE: not high security in the face of local people making
# symlinks out of the static area or whatever.
def doStatic(cfg, reqdata, staticstore):
	# We only serve static files from the normal view.
	# We pretend they don't exist in other ones.
	if reqdata['view'] != 'normal':
		return httputil.genError("out-of-zone")
	path = getStaticPath(cfg, reqdata)
	# We don't serve directories, so the empty path that
	# indicates our root is an immediate exit.
	if not path:
		return httputil.genError("file-not-available")
	po = staticstore.get(path)
	# We flush immediately because we only ever make one
	# call per request to the staticstore.
	staticstore.flush()
	if not po or po.type != "file" or not po.displayable():
		return httputil.genError("file-not-available")
	resp = htmlresp.Response()
	resp.arbitrary(po.contents(), guessContentType(path))
	if po.timestamp() > 0:
		resp.setLastModified(po.timestamp())
		resp.setTimeReliable()
	resp.setContentLength()
	return resp
