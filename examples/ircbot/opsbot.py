import basebot


class OpsHolderBot(basebot.Bot):
    '''A mostly inactive bot, it will just sit in a room and hold any chanops
    you give it, handing out chanops upon request (with the password)

    to get it to op you, /msg it with the password, a space, then the room name

    to start it up, do something like this:
    >>> bot = OpsHolderBot(
    >>>         ("example.irc.server.com", 6667),
    >>>         "bot_nick",
    >>>         password=irc_password_for_nick,
    >>>         ops_password=password_for_giving_chanops,
    >>>         rooms=["#list", "#of", "#channels", "#to", "#serve"])
    >>> bot.run()
    '''
    def __init__(self, *args, **kwargs):
        self._ops_password = kwargs.pop("ops_password")
        super(OpsHolderBot, self).__init__(*args, **kwargs)
        self._chanops = {}

    def join(self, room, passwd=None):
        super(OpsHolderBot, self).join(room, passwd)
        self._chanops[room] = False

    def on_mode(self, cmd, args, prefix):
        parent = super(OpsHolderBot, self)
        if hasattr(parent, "on_mode"):
            parent.on_mode(cmd, args, prefix)

        # MODE is how another user would give us ops -- test for that case
        context, mode_change = args[:2]
        if context.startswith("#") and \
                "o" in mode_change and\
                len(args) > 2 and args[2] == self.nick:
            if mode_change.startswith("+"):
                self._chanops[context] = True
            elif mode_change.startswith("-"):
                self._chanops[context] = False

    def on_privmsg(self, cmd, args, prefix):
        parent = super(OpsHolderBot, self)
        if hasattr(parent, "on_privmsg"):
            parent.on_privmsg(cmd, args, prefix)

        # ignore channels
        if args[0] == self.nick:
            sender = prefix.split("!", 1)[0]

            if " " not in args[1]:
                return
            passwd, roomname = args[1].rsplit(" ", 1)

            if passwd == self._ops_password:
                if self._chanops.get(roomname):
                    # grant chanops
                    self.cmd("mode", roomname, "+o", sender)
                else:
                    self.message(sender,
                            "I don't have ops to give in that room")
            else:
                self.message(sender, "sorry, wrong password")

    def on_reply_353(self, code, args, prefix):
        parent = super(OpsHolderBot, self)
        if hasattr(parent, "on_reply_353"):
            parent.on_reply_353(cmd, args, prefix)

        # 353 is sent when joining a room -- this tests to see if we are given
        # chanops upon entrance (we are the first here, creating the room)
        names = args[-1]
        if "@" + self.nick in names.split(" "):
            self._chanops[args[-2]] = True
