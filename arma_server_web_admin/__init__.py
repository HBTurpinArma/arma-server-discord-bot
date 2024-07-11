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
                    response.json_content = await response.json()
                    return response
        else: #Get all servers
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.config['arma_server_web_admin'][server_type]['address']}api/servers/", headers={'Authorization': "Basic %s" % basic_auth}) as response:
                    response.json_content = await response.json()
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

                