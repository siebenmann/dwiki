#
# Google (and other) sitemaps.
# See https://www.google.com/webmasters/sitemaps/docs/en/protocol.html
#
# This is currently a very simple
import htmlrends, views, httputil

# We list all real files as priority 0.8, in the hopes that Google
# will decide that everything else is at the default, lower priority.
# (We do not attempt to inventory directories or anything else; we
# are only listing real files.)
# We do not include a timestamp because it is going to be misleading.
urlent = """<url>
<loc>%s</loc>
<priority>%s</priority>
</url>
"""

# The commented version of a page is considered more important than
# the plain file version, because it has more information.
comment_pri = '0.9'
file_pri = '0.8'
dir_pri = '0.6'

def genurlent(context, pg, pri):
	return urlent % (httputil.quotehtml(context.nuri(pg)), pri)
def genviewurlent(context, pg, pri, view):
	return urlent % (httputil.quotehtml(context.uri(pg, view = view)), pri)

def minurlset(context):
	"""Generate a Google Sitemap set of <url> entities for the
	directory hierarchy starting at the current directory. Supports
	VirtualDirectory restrictions."""

	# Generate urlset by just going through the page descendants.
	# We generate the <url> list in an arbitrary order, so we don't
	# need to sort the list of pages returned or anything.
	res = []
	dirs = {}
	for ts, pgname in context.page.descendants(context):
		np = context.model.get_page(pgname)
		if np.is_util() or not np.realpage():
			continue

		# We explicitly include all displayable pages, even if
		# they are content-restricted. This may change in the
		# future, but for now we would rather include more URLs.
		# Pages are in the default view (duh).
		ustr = genurlent(context, np, file_pri)
		res.append(ustr)

		# Now, make up the directory entry the first time we
		# see it.
		if np.type == "file":
			dp = np.parent()
			if dp not in dirs:
				dirs[dp] = True
				ustr = genurlent(context, dp, dir_pri)
				res.append(ustr)

		# Generate a reference to the show-comments page if
		# there are comments. Note that this skips comments
		# on undisplayable pages, because of how get_commentlist
		# behaves.
		# If comments-in-normal is set, comments are already
		# shown in the default page view.
		if "comments-in-normal" not in context and \
		   context.model.get_commentlist(np):
			res.append(genviewurlent(context, np, comment_pri,
						 'showcomments'))
	return "".join(res)

htmlrends.register("sitemap::minurlset", minurlset)

# Register the 'sitemap' view.
# Google doesn't say what content-type their sitemap should be
# returned in, so we pick application/xml since, well, it's an
# XML file. The sitemap view is only valid on directories.
class XMLView(views.AltType):
	content_type = "application/xml"

views.register('sitemap', XMLView, onDir = True, onFile = False)
