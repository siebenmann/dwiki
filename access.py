#
# The access control model for dwiki, or at least portions thereof.
#

import utils
import wikirend

access_file = "__access"

def permcheck(startpage, context, what1, what2, default):
	for page in utils.walk_to_root(startpage):
		if page.type == "dir":
			spage = page.child(access_file)
		else:
			spage = page
		if not spage.realpage():
			continue
		res = wikirend.gen_page_features(spage, context)
		if what1 in res:
			return what2 in res
	return default

# By default, people can access page(s).
def access_ok(page, context):
	if not context.model.has_authentication():
		return True
	# 'restricted' is backwards from what we expect; permcheck will
	# return true if the page *is* restricted, ie if access to it is
	# blocked. So we must invert it.
	return not permcheck(page, context, "hasrestricted", "restricted",
			     False)

# This returns True if there is a restriction somewhere on the path
# leading to this page.
def is_restricted(page, context):
	if not context.model.has_authentication():
		return False
	return permcheck(page, context, "hasrestricted", "hasrestricted",
			 False)

# By default, people *cannot* comment.
def comment_ok(page, context):
	if page.type != "file" or \
	   not page.realpage() or \
	   not context.model.comments_on() or \
	   not context.model.has_authentication() or \
	   not context.current_user() or \
	   not access_ok(page, context):
		return False
	return permcheck(page, context, "hascomments", "comments", False)

# This returns true if there is a chance someone, anywhere, might be able
# to comment on the page.
def comments_on(page, context):
	if page.type != "file" or \
	   not page.realpage() or \
	   not context.model.comments_on() or \
	   not context.model.has_authentication():
		return False
	return permcheck(page, context, "hascomments", "hascomments", False)
