#! /usr/bin/python

from twisted.internet import reactor
from foolscap import Copyable, Referenceable, Tub

# the sending side defines the Copyable

class UserRecord(Copyable):
    # this class uses the default Copyable behavior
    typeToCopy = "unique-string-UserRecord"

    def __init__(self, name, age, shoe_size):
        self.name = name
        self.age = age
        self.shoe_size = shoe_size # this is a secret
    
    def getStateToCopy(self):
        d = {}
        d['name'] = self.name
        d['age'] = self.age
        # don't tell anyone our shoe size
        return d

class Database(Referenceable):
    def __init__(self):
        self.users = {}
    def addUser(self, name, age, shoe_size):
        self.users[name] = UserRecord(name, age, shoe_size)
    def remote_getuser(self, name):
        return self.users[name]

db = Database()
db.addUser("alice", 34, 8)
db.addUser("bob", 25, 9)

tub = Tub()
tub.listenOn("tcp:12345")
tub.setLocation("localhost:12345")
url = tub.registerReference(db, "database")
print "the database is at:", url
tub.startService()
reactor.run()
