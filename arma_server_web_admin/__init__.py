from discord.ext import commands
from discord import app_commands
from discord.ext.commands import Context
import discord
import aiohttp
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
import os
import base64
import a2s
import datetime
import aiosqlite

class ArmaServerWebAdmin:
    def __init__(self, *, config, database) -> None:
        self.config = config
        self.database = database

    async def get_user_authentication(self, user_id: str, guild_id: str, server_type: str="arma3") -> None:
        authentication = await self.database.get_server_authentication(user_id, guild_id, server_type)
        #print(authentication, base64.b64encode(f"{authentication['username']}:{authentication['password']}".encode()).decode())
        return base64.b64encode(f"{authentication['username']}:{authentication['password']}".encode()).decode()

    async def get_server_config(self, user_id: str, guild_id: str, server_type: str="arma3", server_id: str=""):
        basic_auth = await self.get_user_authentication(user_id, guild_id, server_type)
        if server_id != "": 
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.config['arma_server_web_admin'][server_type]['address']}api/servers/{server_id}", headers={'Authorization': "Basic %s" % basic_auth}) as response:
                    if response.status == 200:
                        response.json_content = await response.json()
                    else:
                        response.json_content = []
                    return response
        else: #Get all servers
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.config['arma_server_web_admin'][server_type]['address']}api/servers/", headers={'Authorization': "Basic %s" % basic_auth}) as response:
                    if response.status == 200:
                        response.json_content = await response.json()
                    else:
                        response.json_content = []
                    return response

    async def server_start(self, user_id: str, guild_id: str, server_type: str="arma3", server_id: str=""):
        basic_auth = await self.get_user_authentication(user_id, guild_id, server_type)
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.config['arma_server_web_admin'][server_type]['address']}api/servers/{server_id}/start", headers={'Authorization': "Basic %s" % basic_auth}) as response:
                return response

    async def server_stop(self, user_id: str, guild_id: str, server_type: str="arma3", server_id: str=""):
        basic_auth = await self.get_user_authentication(user_id, guild_id, server_type)
        async with aiohttp.ClientSession() as session:
            api = f"{self.config['arma_server_web_admin'][server_type]['address']}api/servers/{server_id}/stop"
            async with session.post(api, headers={'Authorization': "Basic %s" % basic_auth}) as response:
                return response
                
    async def mission_upload(self, user_id: str, guild_id: str, server_type: str="arma3", pbo_upload = None):
        return None
        basic_auth = await self.get_user_authentication(user_id, guild_id, server_type)
        async with aiohttp.ClientSession() as session:
            pass

class ArmaServerDatabaseManager:
    def __init__(self, *, connection: aiosqlite.Connection) -> None:
        self.connection = connection


    async def add_server_status(
        self, guild_id: int, channel_id: int, message_id: int, user_id: int, server_id: str, server_type: str, server_ip: str, server_port: str, server_name: str, server_desc: str="", server_modpack: str=""
    ) -> int:
        """
        This function will add a server status identity to the database, this will be refreshed every 5 minutes.

        :param guild_id: The ID of the guild.
        :param channel_id: The ID of the channel.
        :param message_id: The ID of the message.
        :param user_id: The ID of the user that created the status.

        :param server_id: The ID of the server so we can query the webpanel if we need to.
        :param server_ip: The server IP to query.
        :param server_port: The server port to query.
        :param server_name: The server display name to include in the message.
        :param server_desc: The server display name to include in the message.
        :param server_modpack: The server display name to include in the message.
        """

        await self.connection.execute(
            "INSERT INTO server_status(guild_id, channel_id, message_id, user_id, server_id, server_type, server_ip, server_port, server_name, server_desc, server_modpack) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                guild_id,
                channel_id,
                message_id,
                user_id,
                server_id,
                server_type,
                server_ip,
                server_port,
                server_name,
                server_desc,
                server_modpack
            ),
        )
        await self.connection.commit()
        return True

    async def remove_server_status(self, guild_id: int, channel_id: int, message_id: int) -> int:
        """
        This function will remove a server status identity, deleting the message from the guild.

        :param warn_id: The ID of the warn.
        :param guild_id: The ID of the guild.
        :param message_id: The ID of the message.
        """
        await self.connection.execute(
            "DELETE FROM server_status WHERE guild_id=? AND channel_id=? AND message_id=?",
            (
                guild_id,
                channel_id,
                message_id
            ),
        )
        return True

    async def update_server_status(
        self, guild_id: int, channel_id: int, message_id: int, user_id: int, server_id: str, server_type: str, server_ip: str, server_port: str, server_name: str, server_desc: str="", server_modpack: str=""
    ) -> int:
        """
        This function will update server status in the database.

        :param guild_id: The ID of the guild.
        :param channel_id: The ID of the channel.
        :param message_id: The ID of the message.
        :param user_id: The ID of the user that created the status.

        :param server_id: The ID of the server so we can query the webpanel if we need to.
        :param server_ip: The server IP to query.
        :param server_port: The server port to query.
        :param server_name: The server display name to include in the message.
        :param server_desc: The server display name to include in the message.
        :param server_modpack: The server display name to include in the message.
        """

        await self.connection.execute(
            "UPDATE server_status SET (guild_id, channel_id, message_id, user_id, server_id, server_type, server_ip, server_port, server_name, server_desc, server_modpack) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) WHERE guild_id=? and channel_id=? and message_id=? and server_id=?",
            (
                guild_id,
                channel_id,
                message_id,
                user_id,
                server_id,
                server_type,
                server_ip,
                server_port,
                server_name,
                server_desc,
                server_modpack,
                guild_id,
                channel_id,
                message_id,
                server_id
            ),
        )
        await self.connection.commit()
        return True

    async def get_server_status_from_guild(self, guild_id: int) -> list:
        """
        This function will get all the server status identities for a specific guild.

        :param guild_id: The ID of the guild that should be checked.
        :return: A list of all the server statuses in the guild.
        """
        rows = await self.connection.execute(
            "SELECT guild_id, channel_id, message_id, user_id, server_id, server_type, server_ip, server_port, server_name, server_desc, server_modpack FROM server_status WHERE guild_id=?",
            (
                guild_id
            ),
        )
        async with rows as cursor:
            result = await cursor.fetchall()
            result_list = []
            for row in result:
                result_list.append(list(row))
            return result_list

    async def get_server_status_from_message_id(self, message_id: int) -> list:
        """
        This function will get all the server status identities for a specific guild.

        :param message_id: The ID of the message to check.
        :return: A list of all the server statuses in the guild.
        """
        rows = await self.connection.execute(
            "SELECT guild_id, channel_id, message_id, user_id, server_id, server_type, server_ip, server_port, server_name, server_desc, server_modpack FROM server_status WHERE message_id=?",
            [message_id]
        )
        async with rows as cursor:
            result = await cursor.fetchall()
            result_list = []
            for row in result:
                result_list.append(list(row))
            return result_list

    async def get_server_status_unique_messages(self) -> list:
        """
        This function will get all the server status identities for a specific guild.

        :param guild_id: The ID of the guild that should be checked.
        :return: A list of all the server statuses in the guild.
        """
        rows = await self.connection.execute("SELECT DISTINCT guild_id, channel_id, message_id FROM server_status")
        async with rows as cursor:
            result = await cursor.fetchall()
            result_list = []
            for row in result:
                result_list.append(list(row))
            return result_list

    async def get_server_status(self) -> list:
        """
        This function will get all the server status identities for a specific guild.

        :param guild_id: The ID of the guild that should be checked.
        :return: A list of all the server statuses in the guild.
        """
        rows = await self.connection.execute("SELECT guild_id, channel_id, message_id, user_id, server_id, server_type, server_ip, server_port, server_name, server_desc, server_modpack FROM server_status")
        async with rows as cursor:
            result = await cursor.fetchall()
            result_list = []
            for row in result:
                result_list.append(list(row))
            return result_list


    async def set_server_authentication(
        self, user_id: int, guild_id: int, server_type: str, server_username: str, server_password: str,
    ) -> int:
        """
        This function will setup the authentication details for using the web panel API to stop/start arma servers.

        :param user_id: The ID of the user.
        :param guild_id: The ID of the guild.
        :param server_type: The server type or game reference.
        :param server_username: The web panel username the user uses.
        :param server_password: The web panel password the user uses.a
        """
        await self.connection.execute(
            "DELETE FROM server_authentication WHERE user_id=? AND guild_id=? AND server_type=?",
            (
                user_id,
                guild_id,
                server_type
            ),
        )

        await self.connection.execute(
            "INSERT INTO server_authentication(user_id, guild_id, server_type, server_username, server_password) VALUES (?, ?, ?, ?, ?)",
            (
                user_id,
                guild_id,
                server_type,
                server_username,
                server_password
            ),
        )
        await self.connection.commit()
        return True



    async def get_server_authentication(
        self, user_id: int, guild_id: int, server_type: str
    ) -> int:
        """
        This function will setup the authentication details for using the web panel API to stop/start arma servers.
        
        :param user_id: The ID of the user.
        :param guild_id: The ID of the guild.
        :param server_type: The server type or game reference.
        """
        rows = await self.connection.execute(
            "SELECT server_username, server_password FROM server_authentication WHERE user_id=? AND guild_id=? AND server_type=?",
            (
                user_id,
                guild_id,
                server_type
            ),
        )
        async with rows as cursor:
            result = await cursor.fetchall()
            result_list = []
            for row in result:
                result_list.append(list(row))
            if result_list:
                # print(result_list)
                return {"username": result_list[0][0], "password": result_list[0][1]}
            else:
                return {"username": "", "password": ""}