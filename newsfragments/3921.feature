Automatically exit when stdin is closed

This facilitates subprocess management, specifically cleanup.
When a parent process is running tahoe and exits without time to do "proper" cleanup at least the stdin descriptor will be closed.
Subsequently "tahoe run" notices this and exits.