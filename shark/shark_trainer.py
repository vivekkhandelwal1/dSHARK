# Copyright 2020 The Nod Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
from shark.parser import shark_args
from shark.shark_runner import SharkRunner
from shark.backward_makefx import MakeFxModule
from shark.shark_importer import import_with_fx
import numpy as np
from tqdm import tqdm
import sys

def create_directory_if_not_exists(directory_path):
    if not os.path.exists(directory_path):
        os.makedirs(directory_path)
        print(f"Directory '{directory_path}' created successfully.")
    #else:
        #print(f"Directory '{directory_path}' already exists.")

# Prints to stderr.
def print_err(*a):
    print(*a, file=sys.stderr)


class SharkTrainer:
    """Training pytorch, tensorflow module on shark runtime."""

    def __init__(
        self,
        model,
        input: tuple,
        opt_state_dict,
        dynamic: bool = False,
        device: str = None,
        jit_trace: bool = False,
        from_aot: bool = True,
    ):
        self.model = model
        # Change tuple to list.
        self.input = [x for x in input]
        self.opt_state_dict = opt_state_dict
        self.dynamic = dynamic
        self.from_aot = from_aot
        self.jit_trace = jit_trace
        self.from_aot = from_aot

        # By default it's the torch frontend.
        self.frontend = "pytorch"
        self.device = device if device is not None else shark_args.device

        self.shark_runner = None

    # Sets the frontend i.e `pytorch` or `tensorflow`.
    def set_frontend(self, frontend: str):
        if frontend not in [
            "pytorch",
            "torch",
            "tensorflow",
            "tf",
            "stablehlo",
            "mhlo",
            "linalg",
            "tosa",
        ]:
            print_err("frontend not supported.")
        else:
            self.frontend = frontend

    # Training function is needed in the case of torch_fn.
    def compile(self, training_fn=None, extra_args=[]):
        if self.frontend in ["torch", "pytorch"]:
            packed_inputs = (
                dict(self.model.named_parameters()),
                dict(self.model.named_buffers()),
                self.opt_state_dict,
                tuple(self.input),
            )
            mlir_module, func_name = import_with_fx(
                training_fn, packed_inputs, False, [], training=True,
            )
            self.shark_runner = SharkRunner(
                mlir_module,
                self.device,
                "tm_tensor",
                extra_args=extra_args,
            )
            # To run fx_graph
            # fx_g = import_with_fx(
            #     training_fn, packed_inputs, False, [], training=True, mlir_type="fx",
            # )
            # self.shark_runner = fx_g
        elif self.frontend in ["tensorflow", "tf", "mhlo", "stablehlo"]:
            self.shark_runner = SharkRunner(
                self.model,
                self.input,
                self.dynamic,
                self.device,
                self.jit_trace,
                self.from_aot,
                self.frontend,
            )
        else:
            print_err("Unknown frontend")
            return

    # The inputs to the mlir-graph are weights, buffers and inputs respectively.
    def get_torch_params(self):
        params = [i.detach() for i in self.model.parameters()]
        buffers = [i.detach() for i in self.model.buffers()]
        opt_states = []
        for _, opt_state in self.opt_state_dict.items():
            for i in opt_state:
                opt_states.append(i.detach())
        return params + buffers + opt_states



    # Function to train pytorch module.
    def _train_torch(self, num_iters):
        """Returns the updated weights after num_iters"""
        params = self.get_torch_params()
        # params = [x.numpy() for x in params]
        print(f"Training started for {num_iters} iterations:")
        for i in tqdm(range(num_iters)):
            if i == 0:
                for it in range(30):
                    params[92 + it] += 1
            directory_path = f'bert_training_inputs_bert_8K_0L_adamw{i}'
            create_directory_if_not_exists(directory_path)
            print("num inps: ", len(params))
            for j in range(len(params)):
                np.save(f'{directory_path}/input{j}.npy', params[j])
            params = self.shark_runner.run(
                "forward", params + self.input, self.frontend
            )
            # params = self.shark_runner(
            #     *params, *self.input
            # )
            loss = params[-1]
            print("loss:", loss)
            # print("opt_state_new: ", params[-30:])
            # params_and_buffers = params2 + buffers2 + opt_state2 + loss2
            print("num params: ", len(params))
            # print("buffers2: ", len(buffers2))
            # print("opt_state2: ", len(opt_state2))
            directory_path = f'bert_training_outputs_bert_8K_0L_adamw{i}'
            create_directory_if_not_exists(directory_path)
            for j in range(len(params)):
                np.save(f'{directory_path}/output{j}.npy', params[j])
            params = params[:-1]
        return params

    # Function to train tensorflow module.
    # Output final loss.
    # TODO(raikonenfnu): Save updated weight/states in SHARK.
    def _train_tf(self, num_iters):
        input_list = []
        for x in self.input:
            if isinstance(x, list):
                nested_list = []
                for val in x:
                    if isinstance(val, np.ndarray):
                        nested_list.append(val)
                    else:
                        nested_list.append(val.numpy())
                input_list.append(nested_list)
            elif isinstance(x, np.ndarray):
                input_list.append(x)
            else:
                input_list.append(x.numpy())

        print(f"Training started for {num_iters} iterations:")
        for i in tqdm(range(num_iters)):
            outputs = self.shark_runner.forward(input_list, self.frontend)
        return outputs

    def train(self, num_iters=1):
        if self.frontend in ["torch", "pytorch"]:
            return self._train_torch(num_iters)
        elif self.frontend in ["tf", "tensorflow", "mhlo"]:
            return self._train_tf(num_iters)
        else:
            print_err("Unknown frontend")
            return
