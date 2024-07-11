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

async def get_user_authentication(self, user_id: str, guild_id: str, server_type: str="arma3") -> None:
    authentication = await self.bot.database.get_server_authentication(user_id, guild_id, server_type)
    # print(authentication, base64.b64encode(f"{authentication['username']}:{authentication['password']}".encode()).decode())
    return base64.b64encode(f"{authentication['username']}:{authentication['password']}".encode()).decode()

async def server_start(self, user_id: str, guild_id: str, server_type: str="arma3", server_id: str=""):
    basic_auth = self.get_user_authentication(user_id, guild_id, server_type)
    async with aiohttp.ClientSession() as session:
        api = f"{self.bot.config['arma_server_web_admin'][server_type]['address']}api/servers/{server_id}/start"
        async with session.post(api, headers={'Authorization': "Basic %s" % basic_auth}) as response:
            return response

async def server_stop(self, user_id: str, guild_id: str, server_type: str="arma3", server_id: str=""):
    basic_auth = self.get_user_authentication(user_id, guild_id, server_type)
    async with aiohttp.ClientSession() as session:
        api = f"{self.bot.config['arma_server_web_admin'][server_type]['address']}api/servers/{server_id}/stop"
        async with session.post(api, headers={'Authorization': "Basic %s" % basic_auth}) as response:
            return response