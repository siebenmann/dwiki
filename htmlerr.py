#
# Generate error pages inside a DWiki context, for DWiki pages.
# This requires a DWiki context and so is only intended for use
# inside the DWiki view engine.
#
# DWiki error pages are rendered through special templates using (you
# saw this coming) special renderers. (Clearly the special renderers
# are normally only used in the error page templates.)
#
# Because error generation is important, we fall back to internal
# defaults if an error page template is missing.
#
# By internal convention, the current error is stored in the context
# variable ':error:error'.
#
import htmlrends, template

default_title = "%s - Page Cannot Be Shown"
title_map = {
	'nopage': "%s - Page Not Found",
	'badrequest': "%s - Request Not Understood",
	'badaccess': "%s - Permission Denied",
	}
body_map = {
	'nopage': "Page not found.",
	'badpage': "Page cannot be displayed due to errors.",
	'inconsistpage': "Page is inconsistent and cannot be displayed.",
	'badformat': "Page cannot be displayed in the requested format.",
	'badrequest': "Your request is seriously garbled and cannot be completed.",
	'badaccess': "You do not have sufficient permissions to view this page.",
	}

#
# ...
def errorTemplate(context, suffix = None):
	etype = context[":error:error"]
	if suffix:
		tn = "errors/%s-%s.tmpl" % (etype, suffix)
	else:
		tn = "errors/%s.tmpl" % etype
	return (etype, context.model.get_template(tn, False))

#
# Render a title for the error.
def errortitle(context):
	"""Generate the title for an error from a template in _errors/_,
	if the template exists; otherwise uses a default. Only usable
	during generation of an error page."""
	if ":error:error" not in context:
		return ''
	(etype, to) = errorTemplate(context, "title")
	if to:
		return template.Template(to).render(context)
	elif etype in title_map:
		return title_map[etype] % context[":error:code"]
	else:
		return default_title % context[":error:code"]
htmlrends.register("error::title", errortitle)

# Render a body for the error.
default_body = "<h1> %s - Error Processing Request </h1> <p> %s </p>"
def errorbody(context):
	"""Generates the body for an error page from a template in
	_errors/_, if the template exists; otherwise uses a default.
	Only usable during generation of an error page."""
	if ":error:error" not in context:
		return ''
	(etype, to) = errorTemplate(context)
	if to:
		return template.Template(to).render(context)
	elif etype in body_map:
		return default_body % (context[":error:code"], body_map[etype])
	else:
		# Should we try to generate a better message?
		return default_body % (context[":error:code"],
				       'An internal error has occurred.')
htmlrends.register("error::body", errorbody)

# Generate an error for error in the context, using the response.
# Return the response.
# Errors all try to use the template "error.tmpl", but fall back to
# an internal default if that is missing.
default_error = "<html><head><title>%s</title></head> <body>%s</body></html>"
def error(error, context, resp, code = 404):
	context.setvar(":error:error", error)
	context.setvar(":error:code", "%d" % code)
	context.unrel_time()
	to = context.model.get_template("error.tmpl", False)
	if to:
		res = template.Template(to).render(context)
	else:
		etitle = errortitle(context)
		ebody = errorbody(context)
		res = default_error % (etitle, ebody)
	# helpful reminder to cks at 2am: we really must call error,
	# not html.
	resp.error(res, code)
	return resp

# Support for pages marking themselves as errors.
# Under normal circumstances, aggregation views (blog, blogdir, atom
# feeds...) do not even render pages that the request doesn't have
# access to.
# If someone invokes this in a general page template we should still
# be safe because rendering sub-pages uses a cloned context so the
# set error will never propagate to super-contexts.
#
# We do not mark it as a per-page variable; instead it will act as
# an accumulator of sorts across multiple page renderings using the
# same context. This is me waving my hands a lot.
errorcode = ":error:numcode"
def setpermerror(context):
	"""If we are rendering the top level page of a request (ie, not
	rendering a subpage for blog, blogdir, atom feed, etc context),
	mark this page as having a permission error. This causes the
	page to be reported as a HTTP 403 error."""
	context.setvar(errorcode, 403)
	return ""
htmlrends.register("seterror:permissions", setpermerror)

# retrieve the set error, if any.
def geterror(context):
	if errorcode not in context:
		return 0
	else:
		return context[errorcode]
