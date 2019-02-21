import boto3
import sys
from os import path
from zipfile import ZipFile

HERE = path.dirname(path.abspath(__file__))
script_name = sys.argv[1]
function_name = sys.argv[2]

def upload_function():
    """Upload the lambda function by pushing a zip file to Lambda.

    This function pre-supposes you are running from the same directory that
    contains the lambda script, which should be named: `lambda_script.py`.
    """
    lamb = boto3.client('lambda')
    with ZipFile(path.join(HERE, 'lambda.zip'), 'w') as zf:
        zf.write(path.join(HERE, script_name),
                 'emmaa/{0}/{1}'.format(path.basename(HERE), script_name))
        zf.write(path.join(HERE, '__init__.py'),
                 'emmaa/%s/__init__.py' % path.basename(HERE))
        zf.write(path.join(HERE, path.pardir, '__init__.py'),
                 'emmaa/__init__.py')

    with open(path.join(HERE, 'lambda.zip'), 'rb') as zf:
        ret = lamb.update_function_code(ZipFile=zf.read(),
                                        FunctionName=function_name)
        print(ret)
    return


if __name__ == '__main__':
    upload_function()
