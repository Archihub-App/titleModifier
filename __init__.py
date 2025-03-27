from app.utils.PluginClass import PluginClass
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.utils import DatabaseHandler
from flask import request
from celery import shared_task
from dotenv import load_dotenv
import os
from app.api.records.models import RecordUpdate
from bson.objectid import ObjectId

load_dotenv()

mongodb = DatabaseHandler.DatabaseHandler()
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')

USER_FILES_PATH = os.environ.get('USER_FILES_PATH', '')
WEB_FILES_PATH = os.environ.get('WEB_FILES_PATH', '')
ORIGINAL_FILES_PATH = os.environ.get('ORIGINAL_FILES_PATH', '')

class ExtendedPluginClass(PluginClass):
    def __init__(self, path, import_name, name, description, version, author, type, settings):
        super().__init__(path, __file__, import_name, name, description, version, author, type, settings)

    def add_routes(self):
        @self.route('/bulk', methods=['POST'])
        @jwt_required()
        def process_files():
            current_user = get_jwt_identity()
            body = request.get_json()

            if 'post_type' not in body:
                return {'msg': 'No se especificó el tipo de contenido'}, 400
            
            if not self.has_role('admin', current_user) and not self.has_role('processing', current_user):
                return {'msg': 'No tiene permisos suficientes'}, 401

            task = self.bulk.delay(body, current_user)
            self.add_task_to_user(task.id, 'titleModifier.bulk', current_user, 'msg')
            
            return {'msg': 'Se agregó la tarea a la fila de procesamientos'}, 201
        
    @shared_task(ignore_result=False, name='titleModifier.bulk', queue='low')
    def bulk(body, user):
        from openai import OpenAI
        openai_client = OpenAI(api_key=OPENAI_API_KEY)

        def modify_title(client, original_title, model, instructions, input):
            response = client.responses.create(
            model = model,
            instructions = instructions,
            input = input + original_title
            )
            return response.output_text
        
        filters = {
            'post_type': body['post_type']
        }

        if 'parent' in body:
            if body['parent'] and len(body['resources']) == 0:
                filters = {'$or': [{'parents.id': body['parent'], 'post_type': body['post_type']}, {'_id': ObjectId(body['parent'])}], **filters}
        
        if 'resources' in body:
            if body['resources']:
                if len(body['resources']) > 0:
                    filters = {'_id': {'$in': [ObjectId(resource) for resource in body['resources']]}, **filters}
            
        # obtenemos los recursos
        resources = list(mongodb.get_all_records('resources', filters, fields={'_id': 1, 'metadata': 1}))
        if len(resources) == 0:
            return 'No se encontraron recursos para procesar'
        
        for resource in resources:
            original_title = resource['metadata']['firstLevel']['title']
            new_title = modify_title(openai_client, original_title, body['model'], body['instructions'], body['input'])
            update = {
                'metadata': {
                    'firstLevel': {
                        'title': new_title
                    }
                }
            }
            update_data = RecordUpdate(**update)
            mongodb.update_record('resources', {'_id': resource['_id']}, update_data)

        instance = ExtendedPluginClass('titleModifier','', **plugin_info)
        instance.clear_cache()

        return 'ok'
    
plugin_info = {
    'name': 'Plugin para modificar títulos de recursos usando la API de OpenAI',
    'description': 'Plugin para modificar títulos de recursos usando la API de OpenAI',
    'version': '0.1',
    'author': 'BITSOL SAS',
    'type': ['bulk'],
    'settings': {
        'settings_bulk': [
            {
                'type':  'instructions',
                'title': 'Instrucciones',
                'text': 'Este plugin permite modificar títulos de recursos usando la API de OpenAI. Para usarlo, selecciona los archivos que quieres modificar y configura las opciones del plugin.',
            },
            {
                'type': 'select',
                'label': 'Modelo',
                'id': 'model',
                'default': 'gpt-3.5-turbo',
                'options': [
                    {'value': 'gpt-3.5-turbo', 'label': 'GPT 3.5 Turbo'},
                    {'value': 'gpt-4o', 'label': 'GPT 4o'},
                    {'value': 'gpt-4o-mini', 'label': 'GPT 4o Mini'},
                    {'value': 'gpt-4o-turbo', 'label': 'GPT 4o Turbo'}
                ],
                'required': False,
            },
            {
                'type': 'text',
                'label': 'Instrucciones',
                'id': 'instructions',
                'default': 'Tengo una herramienta de gestión documental con varios recursos y quiero que me ayudes a reescribir el título de esos recursos para que sean más atractivos y llamen la atención de los usuarios usando pocas palabras. Además, debe estar en español.',
                'required': True
            },
            {
                'type': 'text',
                'label': 'Comando para GPT',
                'id': 'input',
                'default': 'Por favor, reescribe el siguiente título de un recurso:',
                'required': True
            }
        ]
    }
}
