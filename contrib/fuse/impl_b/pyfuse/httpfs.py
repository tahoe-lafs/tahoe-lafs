import os, re, urlparse
from handler import Handler
from objectfs import ObjectFs


class Root:
    def __init__(self):
        self.entries = {'gg': GoogleRoot()}
    def listdir(self):
        return self.entries.keys()
    def join(self, hostname):
        if hostname in self.entries:
            return self.entries[hostname]
        if '.' not in hostname:
            raise KeyError
        result = HtmlNode('http://%s/' % (hostname,))
        self.entries[hostname] = result
        return result


class UrlNode:
    data = None

    def __init__(self, url):
        self.url = url

    def getdata(self):
        if self.data is None:
            print self.url
            g = os.popen("lynx -source %r" % (self.url,), 'r')
            self.data = g.read()
            g.close()
        return self.data


class HtmlNode(UrlNode):
    r_links  = re.compile(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                          re.IGNORECASE | re.DOTALL)
    r_images = re.compile(r'<img\s[^>]*src="([^"]+[.]jpg)"', re.IGNORECASE)

    def format(self, text, index,
               TRANSTBL = ''.join([(32<=c<127 and c!=ord('/'))
                                   and chr(c) or '_'
                                   for c in range(256)])):
        return text.translate(TRANSTBL)

    def listdir(self):
        data = self.getdata()

        seen = {}
        def uniquename(name):
            name = self.format(name, len(seen))
            if name == '' or name.startswith('.'):
                name = '_' + name
            basename = name
            i = 1
            while name in seen:
                i += 1
                name = '%s_%d' % (basename, i)
            seen[name] = True
            return name

        for link, text in self.r_links.findall(data):
            url = urlparse.urljoin(self.url, link)
            yield uniquename(text), HtmlNode(url)

        for link in self.r_images.findall(data):
            text = os.path.basename(link)
            url = urlparse.urljoin(self.url, link)
            yield uniquename(text), RawNode(url)

        yield '.source', RawNode(self.url)


class RawNode(UrlNode):

    def read(self):
        return self.getdata()

    def size(self):
        if self.data:
            return len(self.data)
        else:
            return None


class GoogleRoot:
    def join(self, query):
        return GoogleSearch(query)

class GoogleSearch(HtmlNode):
    r_links  = re.compile(r'<a\sclass=l\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                          re.IGNORECASE | re.DOTALL)

    def __init__(self, query):
        self.url = 'http://www.google.com/search?q=' + query

    def format(self, text, index):
        text = text.replace('<b>', '').replace('</b>', '')
        text = HtmlNode.format(self, text, index)
        return '%d. %s' % (index, text)


if __name__ == '__main__':
    root = Root()
    handler = Handler('/home/arigo/mnt', ObjectFs(root))
    handler.loop_forever()
